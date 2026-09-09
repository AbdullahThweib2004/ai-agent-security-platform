"""/forensics — incident reconstruction."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db.postgres import get_session
from app.schemas.errors import NOT_FOUND, UNPROCESSABLE
from app.schemas.forensics import TimelineResponse
from app.services import forensics

router = APIRouter(prefix="/forensics", tags=["forensics"])


@router.get(
    "/timeline/{event_id}",
    response_model=TimelineResponse,
    summary="Reconstruct the full event chain around an event",
    responses={**NOT_FOUND, **UNPROCESSABLE},
)
def timeline(
    event_id: UUID,
    session: Session = Depends(get_session),
) -> TimelineResponse:
    """Walk backward to the root cause and forward through everything it caused."""
    result = forensics.build_timeline(session, event_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"event {event_id} not found",
        )
    return result
