"""Incidents: containment state, and the questions other layers ask about it.

An agent is *suspended* while it has an unresolved incident. That is the whole
definition — there is no separate suspension flag to keep in step with the
incident lifecycle, because two records of one fact drift apart. The partial
unique index on ``incidents`` guarantees at most one unresolved incident per
agent, so this is a lookup rather than a count.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.incident import Incident

# Statuses that mean containment is still in force.
ACTIVE_STATUSES = ("open", "contained")


def active_incident(session: Session, agent_id: str) -> Incident | None:
    """The unresolved incident for an agent, if it has one."""
    return session.scalars(
        select(Incident)
        .where(Incident.agent_id == agent_id)
        .where(Incident.status.in_(ACTIVE_STATUSES))
        .limit(1)
    ).first()


def is_suspended(session: Session, agent_id: str) -> bool:
    """Whether an agent is currently contained.

    Consumed through ``identity.trust_level_of``, so every layer that already
    asks about trust picks suspension up without a second question — and cannot
    disagree with the others about whether an agent is contained.
    """
    return active_incident(session, agent_id) is not None
