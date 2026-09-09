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
from app.models.a2a import A2ADecision
from app.models.delegation import Delegation
from app.models.event import AgentEvent, Alert
from app.schemas.alert import AlertRule
from app.schemas.delegation import DelegationDecision
from app.schemas.errors import (
    BAD_REQUEST,
    CONFLICT,
    NOT_FOUND,
    UNAVAILABLE,
    UNPROCESSABLE,
)
from app.schemas.event import (
    ActionType,
    ActorType,
    AgentEventCreate,
    AgentEventOut,
    AlertOut,
    EventStatus,
    IngestResponse,
    event_to_out,
)
from app.services import (
    a2a_policy,
    delegation_policy,
    incident_policy,
)
from app.services import identity as identity_service
from app.services import incidents as incidents_service
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
        # Record-and-mark. A contained agent's events are still written — they
        # are exactly the events an investigator most wants — but the row
        # carries what was true at ingest, so resolving the incident later
        # cannot make this event stop looking suspended.
        actor_suspended=incidents_service.is_suspended(session, payload.actor_id),
    )
    session.add(event)
    session.flush()

    if event.actor_suspended:
        logger.warning(
            "event recorded from a contained agent",
            extra={
                "event": "event.ingest.from_suspended_agent",
                "event_id": str(event.event_id),
                "actor_id": event.actor_id,
                "target_id": event.target_id,
                "action_type": event.action_type,
                "recorded": True,
            },
        )

    # Identity maintenance, before any policy runs. Every agent the platform
    # observes gets a row, so trust has something to attach to and first/last
    # seen reflect activity rather than administration. Only agents: a user is
    # a person, not an agent identity, and the table is scoped to agents.
    for party_type, party_id in (
        (event.actor_type, event.actor_id),
        (event.target_type, event.target_id),
    ):
        if party_type == ActorType.AGENT.value:
            identity_service.record_activity(session, party_id, event.timestamp)

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

    # Interaction policy runs before delegation policy: it is the gate on
    # whether the two agents should be talking at all, and delegation only
    # matters once they are. Same request, same transaction, same path as the
    # anomaly rules — no separate pipeline.
    a2a_decision = None
    if a2a_policy.applies_to(event):
        a2a_verdict = a2a_policy.decide(session, event)
        a2a_decision = A2ADecision(
            event_id=event.event_id,
            requester_id=a2a_verdict.requester_id,
            target_id=a2a_verdict.target_id,
            decision=a2a_verdict.decision,
            rule=a2a_verdict.rule,
            reason=a2a_verdict.reason,
            requester_trust=a2a_verdict.requester_trust,
            target_trust=a2a_verdict.target_trust,
        )
        session.add(a2a_decision)
        session.flush()

        logger.log(
            logging.WARNING if a2a_verdict.is_blocked else logging.INFO,
            "interaction decided",
            extra={
                "event": "a2a.decided",
                "decision_id": str(a2a_decision.decision_id),
                "event_id": str(event.event_id),
                "requester_id": a2a_verdict.requester_id,
                "target_id": a2a_verdict.target_id,
                "decision": a2a_verdict.decision,
                "rule": a2a_verdict.rule,
                "requester_trust": a2a_verdict.requester_trust,
                "target_trust": a2a_verdict.target_trust,
            },
        )

    # A delegation is still an event, so it runs on this same path rather than a
    # parallel pipeline: same request, same transaction, same staged write as
    # alerts. Only the extra decision is delegation-specific.
    delegation = None
    if event.action_type == ActionType.DELEGATION.value:
        # If interaction policy already refused the conversation, delegation
        # cites that rather than reaching the same conclusion under its own
        # rule name. Its other rules still run: an upstream block says the
        # agents should not be talking, not that the delegator's permissions
        # changed.
        upstream = None
        if a2a_decision is not None and a2a_decision.decision == "blocked":
            upstream = delegation_policy.UpstreamBlock(
                decision_id=str(a2a_decision.decision_id),
                rule=a2a_decision.rule,
                reason=a2a_decision.reason,
            )
        verdict = delegation_policy.decide(session, event, upstream=upstream)
        delegation = Delegation(
            event_id=event.event_id,
            delegator_id=verdict.delegator_id,
            delegate_id=verdict.delegate_id,
            requested_permissions=verdict.requested_permissions,
            granted_permissions=verdict.granted_permissions,
            permission_decisions=verdict.permission_decisions(),
            decision=verdict.decision,
            reason=verdict.reason,
        )
        session.add(delegation)
        session.flush()

        # A delegation the policy refused or trimmed is a security-relevant
        # outcome, not routine traffic — it says an agent asked for more
        # authority than it was allowed to hand on.
        refused = verdict.decision != DelegationDecision.ALLOWED.value
        logger.log(
            logging.WARNING if refused else logging.INFO,
            "delegation decided",
            extra={
                "event": "delegation.decided",
                "delegation_id": str(delegation.delegation_id),
                "event_id": str(event.event_id),
                "delegator_id": verdict.delegator_id,
                "delegate_id": verdict.delegate_id,
                "decision": verdict.decision,
                # Counts and rule names only; the permission names themselves
                # stay in Postgres with the rest of the event detail.
                "requested_count": len(verdict.requested_permissions),
                "granted_count": len(verdict.granted_permissions),
                "rules_applied": sorted({v.rule for v in verdict.verdicts}),
            },
        )

    # Containment runs last of the four policy layers, because it reasons over
    # what the other three just concluded — including about this very event.
    # Fourth consumer of the same ingest path, not a fourth pipeline.
    incident = incident_policy.evaluate(session, event)
    if incident is not None:
        logger.error(
            "agent contained: independent signals crossed the incident threshold",
            extra={
                "event": "incident.opened",
                "incident_id": str(incident.incident_id),
                "agent_id": incident.agent_id,
                "severity": incident.severity,
                "opened_at": incident.opened_at.isoformat(),
                "alert_worthy": True,
                "suspended": True,
                "layers": incident.trigger_summary.get("layers", []),
                "signal_count": incident.trigger_summary.get("signal_count"),
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
