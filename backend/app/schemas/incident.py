"""Incident contracts."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class IncidentStatus(str, Enum):
    """Where an incident is in its lifecycle.

    A closed set, so a mistyped filter is a 422 rather than an empty list.
    """

    OPEN = "open"
    CONTAINED = "contained"
    RESOLVED = "resolved"


class IncidentEventOut(BaseModel):
    model_config = {"from_attributes": True}

    event_id: UUID
    layer: str = Field(description="alert | delegation | a2a")
    detail: str


class IncidentOut(BaseModel):
    model_config = {"from_attributes": True}

    incident_id: UUID
    agent_id: str
    status: IncidentStatus
    severity: str
    opened_at: datetime = Field(
        description="Event time the window closed on — when the agent acted, not "
        "when the platform noticed."
    )
    detected_at: datetime = Field(description="When the platform reached this verdict.")
    trigger_summary: dict[str, Any] = Field(default_factory=dict)
    resolved_at: datetime | None = None
    resolved_by: str | None = None
    resolution_note: str | None = None
    detection_lag_seconds: float = Field(
        description="How long after the agent acted the platform reached this "
        "verdict. Not decoration: a large lag means the signals were replayed or "
        "backfilled, which is exactly the case that makes windowing on ingest "
        "time wrong — and it tells an operator how stale a containment is."
    )
    is_suspended: bool = Field(
        description="Whether this incident is still containing the agent. Derived "
        "from status, not stored separately."
    )
    evidence_by_layer: dict[str, list[IncidentEventOut]] = Field(
        default_factory=dict,
        description="The signals that opened this incident, grouped by the layer "
        "that produced them (alert / delegation / a2a). Corroboration across "
        "layers is the threshold, so the grouping is the shape that matters.",
    )


class IncidentResolution(BaseModel):
    """An operator's explicit release of a contained agent."""

    model_config = {"extra": "forbid"}

    resolved_by: str = Field(
        min_length=1,
        max_length=255,
        description="Who is releasing the agent. Required: containment is only "
        "ever lifted by a named human, never automatically.",
    )
    note: str | None = Field(
        default=None, max_length=2000, description="Why the incident is being closed."
    )
