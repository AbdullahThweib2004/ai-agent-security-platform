"""ORM models for incidents and the evidence that opened them.

An incident is the platform's own conclusion that an agent has gone wrong badly
enough to contain. It differs from every other record here in two ways, and both
shaped the design:

**It is not caused by one event.** Alerts, delegations and A2A decisions each
attach to a single event with a UNIQUE foreign key. An incident is raised on a
*pattern* — several signals from independent layers inside a window — so the
evidence lives in a link table instead.

**It has a lifecycle.** Every other verdict in this system is immutable because
it is evidence. An incident's ``status`` is deliberately mutable: it opens
automatically and is released only by an operator. The evidence half
(``incident_events``, ``trigger_summary``) is still written once and never
revised, so what the platform concluded — and why — survives the status changing.

Incidents are deliberately *not* modelled as agent_events with a new
action_type. ``compute_baseline`` selects every event by ``actor_id``, so an
incident carrying the agent's id would count toward that agent's own behavioural
baseline — inflating its history, and potentially pushing it over the rating
threshold on the strength of incidents raised against it. Letting the platform's
conclusions feed the baselines those conclusions are drawn from is the feedback
loop that has already bitten this codebase three times.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Incident(Base):
    """One containment decision about one agent."""

    __tablename__ = "incidents"

    incident_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    agent_id: Mapped[str] = mapped_column(String(255), nullable=False)

    # open -> contained -> resolved. Mutable, and the only mutable field here
    # besides the resolution pair.
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'open'"), default="open"
    )

    # Event time, not ingest time. The signals that opened this are windowed on
    # when the agent *acted*, not when the platform got round to judging it —
    # replayed or backfilled traffic would otherwise all land in one instant and
    # make every agent look like a burst.
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="high")

    # Immutable evidence: which layers fired, how many signals, over what window.
    trigger_summary: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    # Operator-only release. Auto-expiry would let a compromised agent quietly
    # return, so nothing but an explicit human action clears these.
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    resolved_by: Mapped[str | None] = mapped_column(
        String(255), nullable=True, default=None
    )
    resolution_note: Mapped[str | None] = mapped_column(
        Text, nullable=True, default=None
    )

    evidence = relationship(
        "IncidentEvent",
        back_populates="incident",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_incidents_agent_id", "agent_id"),
        Index("ix_incidents_status", "status"),
        Index("ix_incidents_opened_at", "opened_at"),
        # At most one unresolved incident per agent. Without this, every further
        # signal from an agent already under containment would open another,
        # and "is this agent suspended" would become a question about counting
        # rows rather than finding one.
        Index(
            "ux_incidents_one_active_per_agent",
            "agent_id",
            unique=True,
            postgresql_where=text("status <> 'resolved'"),
        ),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Incident {self.incident_id} {self.agent_id} [{self.status}]>"


class IncidentEvent(Base):
    """One signal that contributed to opening an incident.

    Write-once: an incident's status may change, but the evidence for why it was
    opened must read the same afterwards as it did at the time.
    """

    __tablename__ = "incident_events"

    incident_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("incidents.incident_id", ondelete="CASCADE"),
        primary_key=True,
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("agent_events.event_id", ondelete="CASCADE"),
        primary_key=True,
    )
    # alert | delegation | a2a. Part of the key because one event can contribute
    # through more than one layer — that corroboration is the whole signal.
    layer: Mapped[str] = mapped_column(String(16), primary_key=True)

    detail: Mapped[str] = mapped_column(Text, nullable=False, server_default="")

    incident = relationship("Incident", back_populates="evidence")
    event = relationship("AgentEvent", back_populates="incident_links")

    __table_args__ = (
        Index("ix_incident_events_event_id", "event_id"),
        Index("ix_incident_events_layer", "layer"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<IncidentEvent {self.incident_id} {self.layer} {self.event_id}>"
