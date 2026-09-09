"""Pydantic schemas for the Agent Event contract.

An Agent Event is the atomic unit of the platform: one action taken by an actor
(a user or an agent) against a target (another agent, a tool, an API, a
database). Events chain together via ``parent_event_id`` to form causal traces.

Status is split in two, deliberately:

  reported_status   what the caller claimed when submitting the event.
                    Immutable — the platform never rewrites it.
  platform_status   this platform's own verdict after the rules have run.
                    This is the field every suspicion-aware read path uses.

Keeping them apart means an agent's self-report can never be confused with our
conclusion about it, and a caller that reports everything as ``allowed`` cannot
launder its behaviour through the graph.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
)


class ActorType(str, Enum):
    USER = "user"
    AGENT = "agent"


class TargetType(str, Enum):
    AGENT = "agent"
    TOOL = "tool"
    API = "api"
    DATABASE = "database"


class ActionType(str, Enum):
    TOOL_CALL = "tool_call"
    DELEGATION = "delegation"
    DATA_ACCESS = "data_access"
    API_CALL = "api_call"
    # Agents talking without handing over work: a status query, a health check,
    # a broadcast. This is the surface where interaction policy is the only
    # thing evaluating the exchange, since nothing is being delegated.
    AGENT_MESSAGE = "agent_message"


class EventStatus(str, Enum):
    ALLOWED = "allowed"
    BLOCKED = "blocked"
    SUSPICIOUS = "suspicious"


class AgentEventBase(BaseModel):
    """Identity of an action, independent of how it was judged."""

    model_config = ConfigDict(use_enum_values=False, populate_by_name=True)

    actor_type: ActorType
    actor_id: str = Field(min_length=1, max_length=255)
    target_type: TargetType
    target_id: str = Field(min_length=1, max_length=255)
    action_type: ActionType
    permissions_used: list[str] = Field(default_factory=list)
    # ``metadata`` is reserved on SQLAlchemy's declarative base, so the column is
    # named ``event_metadata`` internally; the wire contract keeps ``metadata``.
    metadata: dict[str, Any] = Field(default_factory=dict)
    parent_event_id: UUID | None = None

    @field_validator("actor_id", "target_id")
    @classmethod
    def _clean_identity(cls, v: str) -> str:
        """Identities are graph keys, so they must be real.

        A whitespace-only id would otherwise become an entity in the behavior
        graph that no human can see, name, or search for.
        """
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("must not be blank or whitespace-only")
        return cleaned

    @field_validator("permissions_used")
    @classmethod
    def _dedupe_permissions(cls, v: list[str]) -> list[str]:
        """Order-preserving dedupe; blank permissions are dropped."""
        seen: set[str] = set()
        out: list[str] = []
        for p in v:
            p = p.strip()
            if p and p not in seen:
                seen.add(p)
                out.append(p)
        return out


class AgentEventCreate(AgentEventBase):
    """Incoming event payload.

    ``event_id`` and ``timestamp`` are optional so callers may supply their own
    (for replay or backfill) or let the server assign them. Only
    ``reported_status`` can be set by a caller — ``platform_status`` is ours to
    decide. ``status`` is accepted as an alias so existing emitters keep working.
    """

    # Unknown fields are rejected rather than dropped. This is an audit log: a
    # caller that misspells `permissions_used` and gets a 201 back believes a
    # permission was recorded when nothing was. Extra context belongs in
    # `metadata`, which is free-form by design.
    model_config = ConfigDict(
        use_enum_values=False, populate_by_name=True, extra="forbid"
    )

    event_id: UUID = Field(default_factory=uuid4)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    reported_status: EventStatus = Field(
        default=EventStatus.ALLOWED,
        validation_alias=AliasChoices("reported_status", "status"),
        description="What the caller claims about this action.",
    )

    @field_validator("timestamp")
    @classmethod
    def _ensure_utc(cls, v: datetime) -> datetime:
        """Naive timestamps are interpreted as UTC rather than rejected."""
        if v.tzinfo is None:
            return v.replace(tzinfo=UTC)
        return v.astimezone(UTC)


class AgentEventOut(AgentEventBase):
    """Event as stored and returned by the API, carrying both verdicts."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    event_id: UUID
    timestamp: datetime
    reported_status: EventStatus = Field(
        description="Immutable: what the caller claimed."
    )
    platform_status: EventStatus = Field(
        description="This platform's verdict after the rules ran. Read this "
        "field to determine suspicion."
    )


class AlertOut(BaseModel):
    """An immutable record of a rule firing against a specific event."""

    model_config = ConfigDict(from_attributes=True)

    alert_id: UUID
    event_id: UUID
    rule_name: str
    severity: str
    triggered_at: datetime
    details: dict[str, Any] = Field(default_factory=dict)


class IngestResponse(BaseModel):
    """Result of ingesting one event, including anything it tripped."""

    event: AgentEventOut
    alerts: list[AlertOut] = Field(default_factory=list)


def event_to_out(event) -> AgentEventOut:
    """Map the ORM row onto the wire contract.

    The column is ``event_metadata`` (``metadata`` is reserved on SQLAlchemy's
    declarative base) while the public field is ``metadata``; this is the one
    place that translation lives.
    """
    return AgentEventOut(
        event_id=event.event_id,
        timestamp=event.timestamp,
        actor_type=event.actor_type,
        actor_id=event.actor_id,
        target_type=event.target_type,
        target_id=event.target_id,
        action_type=event.action_type,
        permissions_used=list(event.permissions_used or []),
        reported_status=event.reported_status,
        platform_status=event.platform_status,
        metadata=dict(event.event_metadata or {}),
        parent_event_id=event.parent_event_id,
    )
