"""ORM model for agent-to-agent interaction decisions.

Delegation asks what authority travels with a handoff. This asks a question that
comes before it: should the two agents be interacting at all? A verdict here
gates the interaction itself, independent of anything being delegated.

Like alerts and delegations, a row is written at ingest and never updated. The
decision must stay readable as it was made, even after the counterparty's trust
level or history changes.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC)


class A2ADecision(Base):
    """One policy decision about one agent-to-agent interaction."""

    __tablename__ = "a2a_decisions"

    decision_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # One verdict per interaction: unique, so a replay cannot produce a second,
    # possibly different, answer for the same event. Same reasoning as
    # delegations.event_id.
    event_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("agent_events.event_id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    requester_id: Mapped[str] = mapped_column(String(255), nullable=False)
    target_id: Mapped[str] = mapped_column(String(255), nullable=False)

    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    rule: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, server_default="")

    # The trust levels the verdict was reached with, recorded because they are
    # mutable on the identity and this row is not: an operator revising a trust
    # level tomorrow must not silently rewrite why something was blocked today.
    requester_trust: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'unrated'")
    )
    target_trust: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'unrated'")
    )

    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    event = relationship("AgentEvent", back_populates="a2a_decision")

    __table_args__ = (
        Index("ix_a2a_decisions_requester_id", "requester_id"),
        Index("ix_a2a_decisions_target_id", "target_id"),
        Index("ix_a2a_decisions_decision", "decision"),
        Index("ix_a2a_decisions_decided_at", "decided_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<A2ADecision {self.decision_id} {self.requester_id}"
            f"->{self.target_id} [{self.decision}] {self.rule}>"
        )
