"""A2A gating and delegation policy, layered.

Interaction policy asks whether two agents should be talking; delegation asks
what authority travels once they are. When the gate closes, delegation cites it
instead of re-deriving the same conclusion — but a closed gate is not a licence
to skip delegation's other reasoning, which is what this matrix pins.

    A2A       delegation would    expected
    -------   -----------------   -------------------------------------------
    blocks    also block          cite upstream, not a duplicate unrated rule
    blocks    have allowed        still blocked, citing upstream
    allows    block (other rule)  that rule still fires
    allows    allow               untouched
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.models.a2a import A2ADecision
from app.models.delegation import Delegation
from tests.conftest import BASE_TIME

pytestmark = pytest.mark.integration

LATER = BASE_TIME + timedelta(hours=2)


def rows_for(session, event_body):
    event_id = event_body["event"]["event_id"]
    return (
        session.query(A2ADecision).filter_by(event_id=event_id).one_or_none(),
        session.query(Delegation).filter_by(event_id=event_id).one_or_none(),
    )


@pytest.fixture
def delegator_with_permissions(post_event, client):
    """finance-agent holds fx:read (benign) and bank:transfer + bank:read."""
    for i, perms in enumerate(
        [["fx:read"], ["fx:read"], ["bank:transfer", "bank:read"], ["fx:read"]]
    ):
        post_event(
            actor_id="finance-agent",
            target_id="fx-rate-tool",
            target_type="tool",
            action_type="tool_call",
            permissions_used=perms,
            timestamp=(BASE_TIME + timedelta(minutes=i)).isoformat(),
        )


def make_rated(post_event, agent_id):
    for i in range(4):
        post_event(
            actor_id=agent_id,
            target_id="bank-api",
            timestamp=(BASE_TIME + timedelta(minutes=10 + i)).isoformat(),
        )


# --- 1. A2A blocks, delegation would also have blocked ----------------------
def test_a2a_blocks_and_delegation_cites_it_instead_of_re_deriving(
    client, post_event, session, delegator_with_permissions
):
    handoff = post_event(
        actor_id="finance-agent",
        target_id="brand-new-agent",
        target_type="agent",
        action_type="delegation",
        permissions_used=[],
        metadata={"requested_permissions": ["fx:read"]},
        timestamp=LATER.isoformat(),
    )
    a2a, delegation = rows_for(session, handoff)

    assert a2a.decision == "blocked"
    assert a2a.rule == "unrated_counterparty"

    assert delegation.decision == "blocked"
    assert str(a2a.decision_id) in delegation.reason, "must reference the a2a row"
    assert delegation.granted_permissions == []

    rules = {v["rule"] for v in delegation.permission_decisions}
    assert rules == {"upstream_a2a_block"}
    assert "unrated_delegate" not in rules, "the redundant re-derivation is gone"


# --- 2. A2A blocks, delegation would have allowed ---------------------------
def test_a2a_blocks_an_otherwise_allowable_delegation(
    client, post_event, session, delegator_with_permissions
):
    """The gate is decisive: nothing travels through a conversation refused."""
    client.post("/agents/shady-agent/trust", json={"trust_level": "external_untrusted"})
    make_rated(post_event, "shady-agent")

    handoff = post_event(
        actor_id="finance-agent",
        target_id="shady-agent",
        target_type="agent",
        action_type="delegation",
        permissions_used=[],
        metadata={"requested_permissions": ["fx:read"]},
        timestamp=LATER.isoformat(),
    )
    a2a, delegation = rows_for(session, handoff)

    assert a2a.decision == "blocked"
    assert a2a.rule == "untrusted_party"
    # Without the gate this would have been a plain allow: finance-agent holds
    # fx:read, it is not sensitive, and the delegate is rated.
    assert delegation.decision == "blocked"
    assert str(a2a.decision_id) in delegation.reason
    assert delegation.permission_decisions[0]["rule"] == "upstream_a2a_block"


# --- 3. A2A allows, delegation blocks on its own grounds --------------------
def test_a2a_allows_but_confinement_still_fires(
    client, post_event, session, delegator_with_permissions
):
    """A permitted conversation does not grant authority nobody holds."""
    client.post("/agents/partner-agent/trust", json={"trust_level": "external_trusted"})

    handoff = post_event(
        actor_id="finance-agent",
        target_id="partner-agent",
        target_type="agent",
        action_type="delegation",
        permissions_used=[],
        metadata={"requested_permissions": ["db:read_pii"]},
        timestamp=LATER.isoformat(),
    )
    a2a, delegation = rows_for(session, handoff)

    assert a2a.decision == "allowed"
    assert delegation.decision == "blocked"
    assert delegation.permission_decisions[0]["rule"] == "confinement"
    assert "never held" in delegation.permission_decisions[0]["reason"]


def test_a2a_allows_but_sensitive_category_still_reduces(
    client, post_event, session, delegator_with_permissions
):
    client.post("/agents/partner-agent/trust", json={"trust_level": "internal"})

    handoff = post_event(
        actor_id="finance-agent",
        target_id="partner-agent",
        target_type="agent",
        action_type="delegation",
        permissions_used=[],
        metadata={"requested_permissions": ["bank:transfer"]},
        timestamp=LATER.isoformat(),
    )
    a2a, delegation = rows_for(session, handoff)

    assert a2a.decision == "allowed"
    assert delegation.decision == "limited"
    verdict = delegation.permission_decisions[0]
    assert verdict["rule"] == "sensitive_category"
    assert verdict["granted_as"] == "bank:read"
    assert delegation.granted_permissions == ["bank:read"]


# --- 4. A2A allows, delegation allows ---------------------------------------
def test_the_ordinary_path_is_untouched(
    client, post_event, session, delegator_with_permissions
):
    client.post("/agents/partner-agent/trust", json={"trust_level": "internal"})

    handoff = post_event(
        actor_id="finance-agent",
        target_id="partner-agent",
        target_type="agent",
        action_type="delegation",
        permissions_used=[],
        metadata={"requested_permissions": ["fx:read"]},
        timestamp=LATER.isoformat(),
    )
    a2a, delegation = rows_for(session, handoff)

    assert a2a.decision == "allowed"
    assert delegation.decision == "allowed"
    assert delegation.granted_permissions == ["fx:read"]
    assert delegation.permission_decisions[0]["rule"] == "default_allow"


# --- the mixed case: a block AND another finding -----------------------------
def test_confinement_outranks_the_upstream_citation(
    client, post_event, session, delegator_with_permissions
):
    """Precedence is unchanged: a permission the delegator never held is a
    different, non-redundant reason and still gets named."""
    handoff = post_event(
        actor_id="finance-agent",
        target_id="brand-new-agent",
        target_type="agent",
        action_type="delegation",
        permissions_used=[],
        metadata={"requested_permissions": ["db:read_pii", "fx:read"]},
        timestamp=LATER.isoformat(),
    )
    a2a, delegation = rows_for(session, handoff)

    assert a2a.decision == "blocked"
    verdicts = {v["permission"]: v["rule"] for v in delegation.permission_decisions}
    assert verdicts["db:read_pii"] == "confinement"
    assert verdicts["fx:read"] == "upstream_a2a_block"
