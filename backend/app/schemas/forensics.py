"""Response schemas for the forensics timeline."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.event import AgentEventOut, AlertOut


class TimelineEntry(BaseModel):
    """One event's place in the reconstructed chain."""

    event: AgentEventOut
    depth: int = Field(description="Hops from the root cause event")
    relation: str = Field(
        description="ancestor | self | descendant | related — how this event "
        "sits relative to the one that was asked about"
    )
    alerts: list[AlertOut] = Field(default_factory=list)


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
