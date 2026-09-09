"""Agent identity, trust, and interaction-decision contracts."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, Field


class TrustLevel(str, Enum):
    """How much an agent is trusted to interact.

    ``unrated`` is derived, not asserted: it is what an agent is until it has
    either accrued enough clean history (see ``services/trust.py``) or been
    classified by an operator. The other three are assertions about
    organisational identity, which no event stream can supply.
    """

    INTERNAL = "internal"
    EXTERNAL_TRUSTED = "external_trusted"
    EXTERNAL_UNTRUSTED = "external_untrusted"
    UNRATED = "unrated"


class AssertableTrustLevel(str, Enum):
    """The levels an operator may assert.

    ``unrated`` is absent deliberately — it is the absence of an assertion, and
    the platform derives it. Offering it here would let an operator "assert"
    that no evidence exists, which is not a claim anyone can make.
    """

    INTERNAL = "internal"
    EXTERNAL_TRUSTED = "external_trusted"
    EXTERNAL_UNTRUSTED = "external_untrusted"


class TrustAssertion(BaseModel):
    """An operator's claim about an agent's organisational identity."""

    model_config = {"extra": "forbid"}

    trust_level: AssertableTrustLevel = Field(
        description="The level to assert. Validated against the enum."
    )


class AgentIdentityOut(BaseModel):
    model_config = {"from_attributes": True}

    agent_id: str
    trust_level: TrustLevel
    first_seen: datetime | None = Field(
        default=None, description="Null if this agent has never appeared in an event"
    )
    last_seen: datetime | None = Field(
        default=None,
        description="Tracks event activity only, never administrative action",
    )
    is_known: bool = Field(
        description="Whether this agent has ever been seen in an event. Derived "
        "from first_seen, not stored."
    )


class A2ADecisionValue(str, Enum):
    """The verdict on one interaction.

    A closed set, so a mistyped filter is a 422 rather than an empty list —
    an empty list is indistinguishable from "nothing was blocked".
    """

    ALLOWED = "allowed"
    BLOCKED = "blocked"


class A2ADecisionOut(BaseModel):
    model_config = {"from_attributes": True}

    decision_id: UUID
    event_id: UUID
    requester_id: str
    target_id: str
    decision: A2ADecisionValue
    rule: str = Field(description="The policy rule that produced this verdict")
    reason: str = Field(description="Why, in plain language")
    requester_trust: TrustLevel = Field(
        description="The requester's trust level *at decision time*. Snapshotted "
        "because the identity is mutable and this record is not."
    )
    target_trust: TrustLevel = Field(
        description="The target's trust level at decision time."
    )
    decided_at: datetime
