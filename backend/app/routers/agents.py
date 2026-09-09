"""/agents — agent identities and operator-asserted trust."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.postgres import get_session
from app.schemas.a2a import AgentIdentityOut, TrustAssertion
from app.schemas.errors import NOT_FOUND, UNPROCESSABLE
from app.services import identity as identity_service

router = APIRouter(prefix="/agents", tags=["agents"])


def _to_out(identity) -> AgentIdentityOut:
    return AgentIdentityOut(
        agent_id=identity.agent_id,
        trust_level=identity.trust_level,
        first_seen=identity.first_seen,
        last_seen=identity.last_seen,
        # Derived, never stored: the two cannot disagree.
        is_known=identity.first_seen is not None,
    )


@router.post(
    "/{agent_id}/trust",
    response_model=AgentIdentityOut,
    summary="Assert an agent's trust level",
    responses={**UNPROCESSABLE},
)
def assert_trust(
    agent_id: str,
    payload: TrustAssertion,
    session: Session = Depends(get_session),
) -> AgentIdentityOut:
    """Classify an agent as internal, external_trusted or external_untrusted.

    Creates the identity if none exists. An operator must be able to mark a
    counterparty untrusted *before* it is ever seen — otherwise a known-bad
    agent gets one free interaction before anyone can act on it.

    ``unrated`` is not assertable: it is the absence of a classification, which
    the platform derives from history rather than accepting as a claim.
    """
    if not agent_id.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="agent_id must not be blank",
        )
    identity = identity_service.assert_trust(
        session, agent_id.strip(), payload.trust_level.value
    )
    session.commit()
    session.refresh(identity)
    return _to_out(identity)


@router.get(
    "/{agent_id}/trust",
    response_model=AgentIdentityOut,
    summary="Read an agent's identity and trust level",
    responses={**NOT_FOUND, **UNPROCESSABLE},
)
def get_trust(
    agent_id: str,
    session: Session = Depends(get_session),
) -> AgentIdentityOut:
    identity = identity_service.get_identity(session, agent_id)
    if identity is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"agent {agent_id!r} has no identity on record",
        )
    return _to_out(identity)
