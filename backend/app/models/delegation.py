"""ORM model for delegation decisions.

When one agent hands work to another, the question is not only *whether* the
handoff happened but *what authority travelled with it*. This table is the
record of that: what the delegate asked for, what it actually received, and
which policy rule decided the difference.

Like alerts, a row is written at ingest and never updated. The decision has to
remain readable as it was made, even after the delegator's own permissions
change.
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


class Delegation(Base):
    """One policy decision about one delegation event."""

    __tablename__ = "delegations"

    delegation_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    # One decision per delegation event: unique, so a replay cannot produce a
    # second, possibly different, verdict for the same handoff.
    event_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("agent_events.event_id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    delegator_id: Mapped[str] = mapped_column(String(255), nullable=False)
    delegate_id: Mapped[str] = mapped_column(String(255), nullable=False)

    requested_permissions: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    granted_permissions: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    # Per-permission verdicts: [{permission, decision, rule, reason, granted_as}]
    # The summary columns above answer "what happened"; this answers "why, for
    # this specific permission", which is what an analyst actually needs.
    permission_decisions: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )

    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    event = relationship("AgentEvent", back_populates="delegation")

    __table_args__ = (
        Index("ix_delegations_delegator_id", "delegator_id"),
        Index("ix_delegations_delegate_id", "delegate_id"),
        Index("ix_delegations_decision", "decision"),
        Index("ix_delegations_decided_at", "decided_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<Delegation {self.delegation_id} {self.delegator_id}"
            f"->{self.delegate_id} [{self.decision}]"
            f" {len(self.granted_permissions or [])}/"
            f"{len(self.requested_permissions or [])} granted>"
        )
