"""ORM model for agent identities.

Every agent the platform has seen is an identity with a trust level. Trust is
hybrid by design: ``unrated`` is *derived* — it falls out of how much clean
history an agent has, via the shared threshold in ``services/trust.py`` — while
``internal`` and ``external_trusted`` are *asserted* by an operator, because
nothing in an event stream can tell you whether an agent belongs to your
organisation.

Unlike alerts, delegations and A2A decisions, this row is mutable: it is a
current-state record, not evidence. ``last_seen`` moves with every event, and an
operator can revise a trust level as circumstances change. The immutable record
of what was decided *because of* a trust level lives in ``a2a_decisions``.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AgentIdentity(Base):
    """One agent, with the trust level the A2A policy judges it by."""

    __tablename__ = "agent_identities"

    # The natural key: the same agent_id used in events, the graph and every
    # other table. A surrogate id would just add a join.
    agent_id: Mapped[str] = mapped_column(String(255), primary_key=True)

    trust_level: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'unrated'"), default="unrated"
    )

    # Nullable, and null means exactly one thing: this agent has never appeared
    # in an event. An operator can assert a trust level before an agent is ever
    # seen — pre-marking a known-bad partner is the point — and stamping "now"
    # on those rows would record an administrative action as activity.
    #
    # This is also what keeps `is_known` off the table: whether an agent has
    # been seen is `first_seen IS NOT NULL`, derived rather than stored, so the
    # two can never disagree.
    first_seen: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    last_seen: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    __table_args__ = (Index("ix_agent_identities_trust_level", "trust_level"),)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<AgentIdentity {self.agent_id} [{self.trust_level}]>"
