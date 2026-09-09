"""Reading and maintaining agent identities.

Trust is hybrid. An operator asserts organisational identity; the platform
derives everything else. This module is the one place those two halves meet, so
no caller has to remember which is which.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from app.models.identity import AgentIdentity
from app.schemas.a2a import TrustLevel


def get_identity(session: Session, agent_id: str) -> AgentIdentity | None:
    return session.get(AgentIdentity, agent_id)


def trust_level_of(session: Session, agent_id: str) -> str:
    """The trust level to judge an agent by.

    An agent with no identity row, or one carrying no assertion, is ``unrated``.
    That is the safe default and the same answer the derived path gives, so an
    agent nobody has classified is never treated as trusted by accident.
    """
    identity = get_identity(session, agent_id)
    if identity is None:
        return TrustLevel.UNRATED.value
    return identity.trust_level


def assert_trust(session: Session, agent_id: str, trust_level: str) -> AgentIdentity:
    """Record an operator's classification, creating the identity if needed.

    Upsert rather than requiring a prior event: pre-marking a known-bad
    counterparty is the point of the endpoint, and an agent that must first
    interact before it can be distrusted would always get one free interaction.

    ``first_seen`` and ``last_seen`` are untouched here. They track observed
    activity; an administrative action is not activity, and writing a timestamp
    into them would make the identity claim an agent had acted when it had not.
    """
    identity = get_identity(session, agent_id)
    if identity is None:
        identity = AgentIdentity(agent_id=agent_id, trust_level=trust_level)
        session.add(identity)
    else:
        identity.trust_level = trust_level
    session.flush()
    return identity


def record_activity(
    session: Session, agent_id: str, seen_at: datetime
) -> AgentIdentity:
    """Note that an agent acted, creating its identity on first sight.

    Only this path moves ``first_seen``/``last_seen``. ``first_seen`` is the
    earliest observation, not the first one ingested, so a backfilled event that
    predates the row moves it backwards rather than being ignored.
    """
    identity = get_identity(session, agent_id)
    if identity is None:
        identity = AgentIdentity(
            agent_id=agent_id,
            trust_level=TrustLevel.UNRATED.value,
            first_seen=seen_at,
            last_seen=seen_at,
        )
        session.add(identity)
        session.flush()
        return identity

    if identity.first_seen is None or seen_at < identity.first_seen:
        identity.first_seen = seen_at
    if identity.last_seen is None or seen_at > identity.last_seen:
        identity.last_seen = seen_at
    session.flush()
    return identity
