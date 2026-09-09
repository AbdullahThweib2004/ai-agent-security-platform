"""/a2a-decisions — interaction verdicts recorded at ingest time."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.postgres import get_session
from app.models.a2a import A2ADecision
from app.schemas.a2a import A2ADecisionOut, A2ADecisionValue
from app.schemas.errors import NOT_FOUND, UNPROCESSABLE

router = APIRouter(prefix="/a2a-decisions", tags=["a2a"])


@router.get(
    "",
    response_model=list[A2ADecisionOut],
    summary="List interaction decisions",
    responses={**UNPROCESSABLE},
)
def list_decisions(
    session: Session = Depends(get_session),
    requester_id: str | None = Query(
        default=None, description="Only interactions initiated by this agent"
    ),
    target_id: str | None = Query(
        default=None, description="Only interactions directed at this agent"
    ),
    decision: A2ADecisionValue | None = Query(
        default=None, description="Only decisions with this outcome"
    ),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[A2ADecisionOut]:
    """Newest-first list of every agent-to-agent interaction judged.

    ``decision`` is a closed set, so a mistyped value is a 422. Agent ids are
    open-ended, so an unknown one is a legitimate empty result rather than an
    error — the same split the delegation routes use.
    """
    stmt = select(A2ADecision)
    if requester_id:
        stmt = stmt.where(A2ADecision.requester_id == requester_id)
    if target_id:
        stmt = stmt.where(A2ADecision.target_id == target_id)
    if decision:
        stmt = stmt.where(A2ADecision.decision == decision.value)
    stmt = stmt.order_by(A2ADecision.decided_at.desc()).limit(limit).offset(offset)
    return [A2ADecisionOut.model_validate(d) for d in session.scalars(stmt)]


@router.get(
    "/{decision_id}",
    response_model=A2ADecisionOut,
    summary="Get one interaction decision, with the trust levels it was made under",
    responses={**NOT_FOUND, **UNPROCESSABLE},
)
def get_decision(
    decision_id: UUID,
    session: Session = Depends(get_session),
) -> A2ADecisionOut:
    """The full record, including both trust snapshots.

    ``requester_trust`` and ``target_trust`` are what the levels *were* when the
    verdict was reached. The identity rows they came from are mutable; this one
    is not, so a later reclassification cannot rewrite why something was decided.

    Delegation records cite this id in their reason when a handoff was refused
    upstream, so a decision found here is the other half of that explanation.
    """
    decision = session.get(A2ADecision, decision_id)
    if decision is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"a2a decision {decision_id} not found",
        )
    return A2ADecisionOut.model_validate(decision)
