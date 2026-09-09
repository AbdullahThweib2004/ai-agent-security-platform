"""/graph — the agent behavior graph."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.db.postgres import get_session
from app.schemas.errors import NOT_FOUND, UNPROCESSABLE
from app.schemas.graph import AgentGraphResponse, GraphResponse
from app.services import graph_service

router = APIRouter(prefix="/graph", tags=["graph"])


@router.get(
    "",
    response_model=GraphResponse,
    summary="Full behavior graph",
    responses={**UNPROCESSABLE},
)
def full_graph(
    session: Session = Depends(get_session),
    limit: int = Query(default=500, ge=1, le=5000),
) -> GraphResponse:
    """Every entity and relationship observed, with suspicion flags."""
    return graph_service.get_full_graph(session, limit=limit)


@router.get(
    "/{agent_id}",
    response_model=AgentGraphResponse,
    summary="Graph scoped to one agent, with its baseline",
    responses={**NOT_FOUND, **UNPROCESSABLE},
)
def agent_graph(
    agent_id: str,
    session: Session = Depends(get_session),
    depth: int = Query(default=1, ge=1, le=4),
) -> AgentGraphResponse:
    """The agent's neighbourhood plus a summary of what is normal for it."""
    result = graph_service.get_agent_graph(session, agent_id, depth=depth)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"agent {agent_id!r} not found in the behavior graph",
        )
    return result
