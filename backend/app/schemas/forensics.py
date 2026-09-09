"""Response schemas for the forensics timeline."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.a2a import A2ADecisionOut
from app.schemas.delegation import DelegationOut
from app.schemas.event import AgentEventOut, AlertOut


class IncidentRef(BaseModel):
    """An incident this event helped open, and which layer it contributed."""

    incident_id: UUID
    agent_id: str
    status: str
    layer: str = Field(description="Which layer this event contributed through")
    detail: str


class TimelineEntry(BaseModel):
    """One event's place in the reconstructed chain."""

    event: AgentEventOut
    depth: int = Field(description="Hops from the root cause event")
    relation: str = Field(
        description="ancestor | self | descendant | related — how this event "
        "sits relative to the one that was asked about"
    )
    alerts: list[AlertOut] = Field(default_factory=list)
    # Everything every policy layer concluded about this event, in one place.
    # Previously only alerts appeared here, so a timeline showed roughly a third
    # of the platform's reasoning: an investigator could see that the incident
    # handoff raised three alerts, but not that delegation refused the authority
    # and interaction policy refused the conversation.
    delegation: DelegationOut | None = Field(
        default=None, description="The delegation verdict, if this was a handoff"
    )
    a2a_decision: A2ADecisionOut | None = Field(
        default=None, description="The interaction verdict, if this was agent-to-agent"
    )
    incidents: list[IncidentRef] = Field(
        default_factory=list,
        description="Incidents this event contributed to opening, and how",
    )


class TimelineResponse(BaseModel):
    requested_event_id: UUID
    root_event_id: UUID
    started_at: datetime
    ended_at: datetime
    event_count: int
    alert_count: int
    participants: list[str] = Field(
        default_factory=list, description="Every entity involved in the chain"
    )
    entries: list[TimelineEntry] = Field(
        default_factory=list, description="Chronological, oldest first"
    )
