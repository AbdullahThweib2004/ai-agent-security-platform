"""Delegation vocabulary and response contracts."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field


class PermissionDecision(str, Enum):
    """The verdict on one requested permission."""

    ALLOWED = "allowed"
    LIMITED = "limited"
    BLOCKED = "blocked"


class DelegationDecision(str, Enum):
    """The verdict on the delegation as a whole."""

    ALLOWED = "allowed"
    LIMITED = "limited"
    BLOCKED = "blocked"


class PermissionVerdictOut(BaseModel):
    permission: str = Field(description="What the delegate asked for")
    decision: PermissionDecision
    rule: str = Field(description="The policy rule that produced this verdict")
    reason: str = Field(description="Why, in plain language")
    granted_as: str | None = Field(
        default=None,
        description="What was actually granted. Differs from `permission` on a "
        "LIMITED verdict (a reduced form); null when nothing was granted.",
    )


class DelegationOut(BaseModel):
    model_config = {"from_attributes": True}

    delegation_id: UUID
    event_id: UUID
    delegator_id: str
    delegate_id: str
    requested_permissions: list[str]
    granted_permissions: list[str]
    permission_decisions: list[PermissionVerdictOut]
    decision: DelegationDecision
    reason: str
    decided_at: datetime
