"""/alerts — anomalies persisted at ingest time."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.postgres import get_session
from app.models.event import AgentEvent, Alert
from app.schemas.alert import AlertRule, AlertSeverity
from app.schemas.errors import NOT_FOUND, UNPROCESSABLE
from app.schemas.event import AgentEventOut, AlertOut, event_to_out

router = APIRouter(prefix="/alerts", tags=["alerts"])


class AlertWithEvent(AlertOut):
    """An alert together with the event that triggered it.

    The UI lists alerts and needs the actor/target context inline; joining here
    saves the client an N+1 walk back to /events.
    """

    event: AgentEventOut


def _join(session: Session, alerts: list[Alert]) -> list[AlertWithEvent]:
    if not alerts:
        return []
    event_ids = {a.event_id for a in alerts}
    events = {
        e.event_id: e
        for e in session.scalars(
            select(AgentEvent).where(AgentEvent.event_id.in_(event_ids))
        )
    }
    return [
        AlertWithEvent(
            **AlertOut.model_validate(a).model_dump(),
            event=event_to_out(events[a.event_id]),
        )
        for a in alerts
        if a.event_id in events
    ]


@router.get(
    "",
    response_model=list[AlertWithEvent],
    summary="List alerts",
    responses={**UNPROCESSABLE},
)
def list_alerts(
    session: Session = Depends(get_session),
    agent_id: str | None = Query(
        default=None, description="Filter by the actor that triggered it"
    ),
    rule_name: AlertRule | None = Query(
        default=None, description="Only alerts raised by this rule"
    ),
    severity: AlertSeverity | None = Query(
        default=None, description="Only alerts at this severity"
    ),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[AlertWithEvent]:
    """Newest-first list of everything the rules have flagged."""
    stmt = select(Alert)
    if agent_id:
        stmt = stmt.join(AgentEvent, Alert.event_id == AgentEvent.event_id).where(
            AgentEvent.actor_id == agent_id
        )
    if rule_name:
        stmt = stmt.where(Alert.rule_name == rule_name.value)
    if severity:
        stmt = stmt.where(Alert.severity == severity.value)
    stmt = stmt.order_by(Alert.triggered_at.desc()).limit(limit).offset(offset)
    return _join(session, list(session.scalars(stmt)))


@router.get(
    "/{alert_id}",
    response_model=AlertWithEvent,
    summary="Get one alert",
    responses={**NOT_FOUND, **UNPROCESSABLE},
)
def get_alert(
    alert_id: UUID,
    session: Session = Depends(get_session),
) -> AlertWithEvent:
    alert = session.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"alert {alert_id} not found",
        )
    joined = _join(session, [alert])
    return joined[0]
