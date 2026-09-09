"""ORM models for the durable event log and the immutable alert record."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class AgentEvent(Base):
    """One action taken by an actor against a target.

    Events are append-only: the log is evidence, so nothing here is updated
    after insert. Causality is expressed by ``parent_event_id``, a self
    reference that lets a trace be walked in both directions.
    """

    __tablename__ = "agent_events"

    event_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)
    target_id: Mapped[str] = mapped_column(String(255), nullable=False)

    action_type: Mapped[str] = mapped_column(String(32), nullable=False)
    permissions_used: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=text("'{}'::text[]")
    )
    # Two distinct facts that were previously conflated in one column.
    # ``reported_status`` is the caller's own claim and is never rewritten;
    # ``platform_status`` is this platform's verdict and is what every
    # suspicion-aware read path (graph, alerts, agent list) consults.
    reported_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="allowed"
    )
    platform_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="allowed"
    )

    # ``metadata`` is reserved on the declarative base, so the attribute and
    # column are named ``event_metadata``; the API contract still says
    # ``metadata`` (translated in the router).
    event_metadata: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    # Postgres is the durable log and Neo4j is a projection of it; the two
    # cannot share a transaction. This flag is the seam: it is set only after
    # the graph write actually commits, so an event that survived the crash
    # window is discoverable and can be re-projected. See services/reconcile.py.
    graph_projected: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    parent_event_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("agent_events.event_id", ondelete="SET NULL"),
        nullable=True,
    )

    parent = relationship(
        "AgentEvent",
        remote_side=[event_id],
        back_populates="children",
        uselist=False,
    )
    children = relationship(
        "AgentEvent",
        back_populates="parent",
        cascade="save-update",
    )
    alerts = relationship(
        "Alert",
        back_populates="event",
        cascade="all, delete-orphan",
    )
    delegation = relationship(
        "Delegation",
        back_populates="event",
        cascade="all, delete-orphan",
        uselist=False,
    )
    a2a_decision = relationship(
        "A2ADecision",
        back_populates="event",
        cascade="all, delete-orphan",
        uselist=False,
    )

    __table_args__ = (
        Index("ix_agent_events_actor_id", "actor_id"),
        Index("ix_agent_events_timestamp", "timestamp"),
        Index("ix_agent_events_parent_event_id", "parent_event_id"),
        # Baselines scan an agent's history in time order; this composite index
        # serves that access pattern directly.
        Index("ix_agent_events_actor_timestamp", "actor_id", "timestamp"),
        Index("ix_agent_events_target_id", "target_id"),
        # The reconciler seeks on this; it is almost always empty, so a partial
        # index keeps it tiny no matter how large the log grows.
        Index(
            "ix_agent_events_unprojected",
            "graph_projected",
            postgresql_where=text("graph_projected = false"),
        ),
        Index("ix_agent_events_platform_status", "platform_status"),
        Index("ix_agent_events_reported_status", "reported_status"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<AgentEvent {self.event_id} {self.actor_id}"
            f" -{self.action_type}-> {self.target_id} [{self.platform_status}]>"
        )


class Alert(Base):
    """An immutable record that a rule fired against a specific event.

    Persisted at ingest rather than derived at query time so forensics can point
    at a stable ID and show what was flagged *at the time*, even if the agent's
    baseline has since shifted.
    """

    __tablename__ = "alerts"

    alert_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    event_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("agent_events.event_id", ondelete="CASCADE"),
        nullable=False,
    )
    rule_name: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, default="medium")
    triggered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    details: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    event = relationship("AgentEvent", back_populates="alerts")

    __table_args__ = (
        Index("ix_alerts_event_id", "event_id"),
        Index("ix_alerts_triggered_at", "triggered_at"),
        Index("ix_alerts_rule_name", "rule_name"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Alert {self.alert_id} {self.rule_name} on {self.event_id}>"
