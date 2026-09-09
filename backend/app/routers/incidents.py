"""/incidents — containment decisions and their operator-only release."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.postgres import get_session
from app.models.incident import Incident
from app.schemas.errors import BAD_REQUEST, NOT_FOUND, UNPROCESSABLE
from app.schemas.incident import (
    IncidentEventOut,
    IncidentOut,
    IncidentResolution,
    IncidentStatus,
)
from app.services.incidents import ACTIVE_STATUSES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/incidents", tags=["incidents"])


def _to_out(incident: Incident) -> IncidentOut:
    return IncidentOut(
        incident_id=incident.incident_id,
        agent_id=incident.agent_id,
        status=incident.status,
        severity=incident.severity,
        opened_at=incident.opened_at,
        detected_at=incident.detected_at,
        trigger_summary=incident.trigger_summary or {},
        resolved_at=incident.resolved_at,
        resolved_by=incident.resolved_by,
        resolution_note=incident.resolution_note,
        detection_lag_seconds=(
            incident.detected_at - incident.opened_at
        ).total_seconds(),
        # Derived from status, never stored: two records of one fact drift.
        is_suspended=incident.status in ACTIVE_STATUSES,
        evidence_by_layer=_group_evidence(incident),
    )


def _group_evidence(incident: Incident) -> dict[str, list[IncidentEventOut]]:
    """Signals grouped by the layer that produced them.

    Corroboration across layers is what the threshold turns on, so this is the
    shape an operator needs: not "five signals" but "three layers agreed".
    """
    grouped: dict[str, list[IncidentEventOut]] = {}
    for link in sorted(incident.evidence, key=lambda e: (e.layer, str(e.event_id))):
        grouped.setdefault(link.layer, []).append(
            IncidentEventOut(
                event_id=link.event_id, layer=link.layer, detail=link.detail
            )
        )
    return grouped


@router.get(
    "",
    response_model=list[IncidentOut],
    summary="List incidents",
    responses={**UNPROCESSABLE},
)
def list_incidents(
    session: Session = Depends(get_session),
    agent_id: str | None = Query(
        default=None, description="Only incidents for this agent"
    ),
    status_filter: IncidentStatus | None = Query(
        default=None, alias="status", description="Only incidents in this state"
    ),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[IncidentOut]:
    """Newest-first list of every containment decision.

    ``status`` is a closed set, so a mistyped value is a 422 rather than an
    empty list. Agent ids are open-ended, so an unknown one is a legitimate
    empty result.
    """
    stmt = select(Incident)
    if agent_id:
        stmt = stmt.where(Incident.agent_id == agent_id)
    if status_filter:
        stmt = stmt.where(Incident.status == status_filter.value)
    stmt = stmt.order_by(Incident.opened_at.desc()).limit(limit).offset(offset)
    return [_to_out(i) for i in session.scalars(stmt)]


@router.get(
    "/{incident_id}",
    response_model=IncidentOut,
    summary="Get one incident, with the signals that opened it",
    responses={**NOT_FOUND, **UNPROCESSABLE},
)
def get_incident(
    incident_id: UUID,
    session: Session = Depends(get_session),
) -> IncidentOut:
    """The full record: what tripped, across which layers, over what window."""
    incident = session.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"incident {incident_id} not found",
        )
    return _to_out(incident)


@router.post(
    "/{incident_id}/resolve",
    response_model=IncidentOut,
    summary="Release a contained agent (operator action)",
    responses={**NOT_FOUND, **BAD_REQUEST, **UNPROCESSABLE},
)
def resolve_incident(
    incident_id: UUID,
    payload: IncidentResolution,
    session: Session = Depends(get_session),
) -> IncidentOut:
    """Lift containment. This is the only way an agent comes back.

    Deliberately manual and deliberately attributed. Containment opens
    automatically because a threat should not wait for a human; it closes only
    when a named person says so, because auto-expiry would let a compromised
    agent quietly return. The asymmetry is the point — the recoverable error is
    a legitimate agent staying stopped until someone looks.
    """
    incident = session.get(Incident, incident_id)
    if incident is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"incident {incident_id} not found",
        )
    if incident.status == IncidentStatus.RESOLVED.value:
        # Not a silent no-op: an operator who believes they just released an
        # agent, when someone else released it hours ago, is missing something.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"incident {incident_id} was already resolved at "
                f"{incident.resolved_at.isoformat()} by {incident.resolved_by}"
            ),
        )

    incident.status = IncidentStatus.RESOLVED.value
    incident.resolved_at = datetime.now(UTC)
    incident.resolved_by = payload.resolved_by.strip()
    incident.resolution_note = payload.note
    session.commit()
    session.refresh(incident)

    logger.warning(
        "containment lifted by an operator",
        extra={
            "event": "incident.resolved",
            "incident_id": str(incident.incident_id),
            "agent_id": incident.agent_id,
            "resolved_by": incident.resolved_by,
            "suspended": False,
        },
    )
    return _to_out(incident)
