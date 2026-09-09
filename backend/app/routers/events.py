"""/events — ingestion and retrieval of agent events."""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.neo4j import get_driver, project_event
from app.db.postgres import get_session
from app.models.event import AgentEvent, Alert
from app.schemas.alert import AlertRule
from app.schemas.errors import (
    BAD_REQUEST,
    CONFLICT,
    NOT_FOUND,
    UNAVAILABLE,
    UNPROCESSABLE,
)
from app.schemas.event import (
    AgentEventCreate,
    AgentEventOut,
    AlertOut,
    EventStatus,
    IngestResponse,
    event_to_out,
)
from app.services.anomaly import evaluate
from app.services.baseline import compute_baseline
from app.services.reconcile import reconcile as run_reconcile

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/events", tags=["events"])

# Every rule the detector runs, for the "which rules ran" log line.
ALL_RULE_NAMES = [rule.value for rule in AlertRule]


@router.post(
    "",
    response_model=IngestResponse,
    status_code=http_status.HTTP_201_CREATED,
    summary="Ingest an agent event",
    responses={**BAD_REQUEST, **CONFLICT, **UNPROCESSABLE, **UNAVAILABLE},
)
def ingest_event(
    payload: AgentEventCreate,
    session: Session = Depends(get_session),
) -> IngestResponse:
    """Validate, store, project, and judge a single agent event.

    Postgres is the durable log and Neo4j is a projection of it, so the two
    writes cannot share a transaction. They are staged instead: the graph write
    is prepared and only committed once Postgres has committed. A failure on
    either side leaves no half-written event, and because the projection is
    MERGE-based, replaying an event after a crash converges rather than
    duplicating.
    """
    log_context = {
        "event": "event.ingest.received",
        "event_id": str(payload.event_id),
        "actor_id": payload.actor_id,
        "actor_type": payload.actor_type.value,
        "target_id": payload.target_id,
        "target_type": payload.target_type.value,
        "action_type": payload.action_type.value,
        "reported_status": payload.reported_status.value,
        # Counts, not contents: the permissions themselves live in Postgres.
        "permission_count": len(payload.permissions_used),
        "has_parent": payload.parent_event_id is not None,
    }
    logger.info("event received", extra=log_context)

    if session.get(AgentEvent, payload.event_id) is not None:
        logger.warning(
            "event rejected: duplicate",
            extra={
                **log_context,
                "event": "event.ingest.rejected",
                "reason": "duplicate",
            },
        )
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail=f"event {payload.event_id} already ingested",
        )

    if payload.parent_event_id is not None:
        if payload.parent_event_id == payload.event_id:
            logger.warning(
                "event rejected: self-referencing parent",
                extra={
                    **log_context,
                    "event": "event.ingest.rejected",
                    "reason": "self_referencing_parent",
                },
            )
            # Would be caught below as "parent does not exist", but that message
            # sends the caller looking for a missing event rather than at the
            # actual mistake.
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"event {payload.event_id} cannot be its own parent; "
                    "parent_event_id must reference a different, existing event"
                ),
            )
        if session.get(AgentEvent, payload.parent_event_id) is None:
            logger.warning(
                "event rejected: unknown parent",
                extra={
                    **log_context,
                    "event": "event.ingest.rejected",
                    "reason": "unknown_parent",
                    "parent_event_id": str(payload.parent_event_id),
                },
            )
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail=f"parent_event_id {payload.parent_event_id} does not exist",
            )

    event = AgentEvent(
        event_id=payload.event_id,
        timestamp=payload.timestamp,
        actor_type=payload.actor_type.value,
        actor_id=payload.actor_id,
        target_type=payload.target_type.value,
        target_id=payload.target_id,
        action_type=payload.action_type.value,
        permissions_used=payload.permissions_used,
        reported_status=payload.reported_status.value,
        platform_status=payload.reported_status.value,
        event_metadata=payload.metadata,
        parent_event_id=payload.parent_event_id,
    )
    session.add(event)
    session.flush()

    # The baseline is built strictly from what came *before* this event, so an
    # anomalous event can never be used to normalise itself.
    baseline = compute_baseline(
        session,
        payload.actor_id,
        before=payload.timestamp,
        exclude_event_id=payload.event_id,
    )
    findings = evaluate(event, baseline)

    fired = [f.rule_name for f in findings]
    evaluated = ALL_RULE_NAMES if baseline.is_established else []
    logger.info(
        "rules evaluated",
        extra={
            "event": "event.rules.evaluated",
            "event_id": str(payload.event_id),
            "actor_id": payload.actor_id,
            "baseline_established": baseline.is_established,
            "baseline_event_count": baseline.event_count,
            "rules_evaluated": evaluated,
            "rules_fired": fired,
            "rules_passed": [r for r in evaluated if r not in fired],
            # A cold-start agent is judged by nothing; say so explicitly rather
            # than letting an empty result look like a clean bill of health.
            "skipped_reason": (
                None if baseline.is_established else "baseline_not_established"
            ),
        },
    )

    # A tripped rule is the platform's own verdict. It is recorded separately
    # from what the caller claimed: reported_status is left exactly as it
    # arrived, while platform_status carries our conclusion. A caller that
    # reported 'blocked' keeps that stronger verdict — a rule firing cannot
    # downgrade an action the caller already refused.
    if findings and event.platform_status == EventStatus.ALLOWED.value:
        event.platform_status = EventStatus.SUSPICIOUS.value

    alerts = [
        Alert(
            event_id=event.event_id,
            rule_name=f.rule_name,
            severity=f.severity,
            details=f.details,
        )
        for f in findings
    ]
    for alert in alerts:
        session.add(alert)
    session.flush()

    for finding, alert in zip(findings, alerts, strict=True):
        logger.warning(
            "alert raised",
            extra={
                "event": "alert.raised",
                "alert_id": str(alert.alert_id),
                "event_id": str(event.event_id),
                "actor_id": event.actor_id,
                "target_id": event.target_id,
                "target_type": event.target_type,
                "rule": finding.rule_name,
                "severity": finding.severity,
                # The evidence (`details`) is intentionally not logged: it
                # restates amounts, queries and counterparties. It is durable
                # in Postgres and reachable via GET /alerts/{alert_id}.
            },
        )

    driver = get_driver()
    with driver.session() as neo_session:
        neo_tx = neo_session.begin_transaction()
        try:
            project_event(
                event_id=str(event.event_id),
                timestamp=event.timestamp,
                actor_type=event.actor_type,
                actor_id=event.actor_id,
                target_type=event.target_type,
                target_id=event.target_id,
                action_type=event.action_type,
                permissions_used=list(event.permissions_used or []),
                platform_status=event.platform_status,
                tx=neo_tx,
            )
            # Only true once the graph transaction below actually commits.
            event.graph_projected = False
            session.commit()
        except Exception as exc:
            neo_tx.rollback()
            session.rollback()
            logger.error(
                "ingest failed; nothing was written",
                exc_info=True,
                extra={
                    "event": "event.ingest.failed",
                    "event_id": str(payload.event_id),
                    "actor_id": payload.actor_id,
                    "stage": "staged_write",
                    "postgres_write": "rolled_back",
                    "neo4j_write": "rolled_back",
                    "error_type": type(exc).__name__,
                    "error": str(exc)[:300],
                },
            )
            raise HTTPException(
                status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="event could not be recorded; nothing was written",
            ) from exc
        else:
            try:
                neo_tx.commit()
            except Exception as exc:
                # Postgres already holds the event. Discarding a durable
                # security event because a projection failed would be worse
                # than a temporarily incomplete graph, so the event stands and
                # stays flagged as unprojected for the reconciler to repair.
                logger.error(
                    "postgres committed but graph projection failed; "
                    "event left unprojected for reconciliation",
                    exc_info=True,
                    extra={
                        "event": "event.ingest.graph_commit_failed",
                        "event_id": str(event.event_id),
                        "actor_id": event.actor_id,
                        "target_id": event.target_id,
                        "stage": "graph_commit",
                        "postgres_write": "committed",
                        "neo4j_write": "failed",
                        "graph_projected": False,
                        "remediation": "POST /events/reconcile",
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:300],
                    },
                )
            else:
                event.graph_projected = True
                session.commit()
                logger.info(
                    "event ingested",
                    extra={
                        "event": "event.ingest.completed",
                        "event_id": str(event.event_id),
                        "actor_id": event.actor_id,
                        "target_id": event.target_id,
                        "action_type": event.action_type,
                        "reported_status": event.reported_status,
                        "platform_status": event.platform_status,
                        "status_overridden": event.reported_status
                        != event.platform_status,
                        "alert_count": len(alerts),
                        "postgres_write": "committed",
                        "neo4j_write": "committed",
                        "graph_projected": True,
                    },
                )

    session.refresh(event)
    return IngestResponse(
        event=event_to_out(event),
        alerts=[AlertOut.model_validate(a) for a in alerts],
    )


@router.post(
    "/reconcile",
    summary="Re-project events that never reached the graph",
    responses={**UNPROCESSABLE, **UNAVAILABLE},
)
def reconcile_events(
    session: Session = Depends(get_session),
    limit: int = Query(default=500, ge=1, le=5000),
) -> dict:
    """Repair the one window the staged write cannot close atomically.

    Postgres is the source of truth; the graph is a projection of it. If the
    graph commit failed after Postgres committed, the event is durable but
    invisible in the graph. Replaying it is safe because every projection is a
    MERGE, so this converges rather than duplicating.
    """
    return run_reconcile(session, limit=limit)


@router.get(
    "",
    response_model=list[AgentEventOut],
    summary="List events",
    responses={**UNPROCESSABLE},
)
def list_events(
    session: Session = Depends(get_session),
    actor_id: str | None = Query(default=None),
    target_id: str | None = Query(default=None),
    platform_status: EventStatus | None = Query(
        default=None, description="Filter on the platform's verdict"
    ),
    reported_status: EventStatus | None = Query(
        default=None, description="Filter on what the caller claimed"
    ),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[AgentEventOut]:
    """Newest-first event log, optionally filtered."""
    stmt = select(AgentEvent)
    if actor_id:
        stmt = stmt.where(AgentEvent.actor_id == actor_id)
    if target_id:
        stmt = stmt.where(AgentEvent.target_id == target_id)
    if platform_status:
        stmt = stmt.where(AgentEvent.platform_status == platform_status.value)
    if reported_status:
        stmt = stmt.where(AgentEvent.reported_status == reported_status.value)
    stmt = stmt.order_by(AgentEvent.timestamp.desc()).limit(limit).offset(offset)
    return [event_to_out(e) for e in session.scalars(stmt)]


@router.get(
    "/{event_id}",
    response_model=AgentEventOut,
    summary="Get one event",
    responses={**NOT_FOUND, **UNPROCESSABLE},
)
def get_event(
    event_id: UUID,
    session: Session = Depends(get_session),
) -> AgentEventOut:
    event = session.get(AgentEvent, event_id)
    if event is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"event {event_id} not found",
        )
    return event_to_out(event)
