"""/delegations — least-privilege decisions recorded at ingest time."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.postgres import get_session
from app.models.delegation import Delegation
from app.schemas.delegation import DelegationDecision, DelegationOut
from app.schemas.errors import NOT_FOUND, UNPROCESSABLE

router = APIRouter(prefix="/delegations", tags=["delegations"])


@router.get(
    "",
    response_model=list[DelegationOut],
    summary="List delegation decisions",
    responses={**UNPROCESSABLE},
)
def list_delegations(
    session: Session = Depends(get_session),
    delegator_id: str | None = Query(
        default=None, description="Only handoffs made by this agent"
    ),
    delegate_id: str | None = Query(
        default=None, description="Only handoffs received by this agent"
    ),
    decision: DelegationDecision | None = Query(
        default=None, description="Only decisions with this outcome"
    ),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[DelegationOut]:
    """Newest-first list of every delegation the policy engine has judged.

    ``decision`` is a closed set, so a mistyped value is a 422 rather than an
    empty list — an empty list is indistinguishable from "nothing was blocked",
    which is exactly the answer that would hide a problem.
    """
    stmt = select(Delegation)
    if delegator_id:
        stmt = stmt.where(Delegation.delegator_id == delegator_id)
    if delegate_id:
        stmt = stmt.where(Delegation.delegate_id == delegate_id)
    if decision:
        stmt = stmt.where(Delegation.decision == decision.value)
    stmt = stmt.order_by(Delegation.decided_at.desc()).limit(limit).offset(offset)
    return [DelegationOut.model_validate(d) for d in session.scalars(stmt)]


@router.get(
    "/{delegation_id}",
    response_model=DelegationOut,
    summary="Get one delegation decision, with per-permission reasoning",
    responses={**NOT_FOUND, **UNPROCESSABLE},
)
def get_delegation(
    delegation_id: UUID,
    session: Session = Depends(get_session),
) -> DelegationOut:
    """The full record: what was asked for, what was granted, and why.

    ``permission_decisions`` carries a verdict per requested permission, each
    naming the rule that produced it — the summary fields say what happened, this
    says why it happened to that specific permission.
    """
    delegation = session.get(Delegation, delegation_id)
    if delegation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"delegation {delegation_id} not found",
        )
    return DelegationOut.model_validate(delegation)
