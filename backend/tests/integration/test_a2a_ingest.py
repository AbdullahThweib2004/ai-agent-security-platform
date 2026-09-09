"""A2A evaluation at ingest, against real Postgres."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.models.a2a import A2ADecision
from tests.conftest import BASE_TIME

pytestmark = pytest.mark.integration

LATER = BASE_TIME + timedelta(hours=2)


def decision_for(session, event_body):
    event_id = event_body["event"]["event_id"]
    return session.query(A2ADecision).filter_by(event_id=event_id).one_or_none()


@pytest.fixture
def rated_agents(seed_baseline):
    """Two agents with enough clean history to be rated."""
    seed_baseline(actor_id="payment-agent", permissions=("bank:transfer",))
    seed_baseline(
        actor_id="finance-agent",
        target_id="fx-rate-tool",
        target_type="tool",
        permissions=("fx:read",),
    )


# --- scope ------------------------------------------------------------------
def test_agent_to_agent_events_get_a_verdict(client, post_event, session, rated_agents):
    body = post_event(
        actor_id="payment-agent",
        target_id="finance-agent",
        target_type="agent",
        action_type="agent_message",
        permissions_used=["agent:status"],
        timestamp=LATER.isoformat(),
    )
    decision = decision_for(session, body)
    assert decision is not None
    assert decision.requester_id == "payment-agent"
    assert decision.target_id == "finance-agent"
    assert decision.decision == "allowed"


@pytest.mark.parametrize(
    "kw",
    [
        dict(target_type="tool", target_id="fx-rate-tool", action_type="tool_call"),
        dict(target_type="api", target_id="bank-api", action_type="api_call"),
        dict(
            target_type="database", target_id="payments-db", action_type="data_access"
        ),
        dict(
            actor_type="user",
            actor_id="analyst-1",
            target_type="agent",
            target_id="finance-agent",
            action_type="delegation",
        ),
    ],
)
def test_non_agent_to_agent_events_get_no_verdict(client, post_event, session, kw):
    body = post_event(timestamp=LATER.isoformat(), **kw)
    assert decision_for(session, body) is None


def test_delegations_between_agents_are_also_gated(
    client, post_event, session, rated_agents
):
    """A delegation is an interaction before it is a handoff."""
    body = post_event(
        actor_id="payment-agent",
        target_id="finance-agent",
        target_type="agent",
        action_type="delegation",
        permissions_used=["bank:transfer"],
        timestamp=LATER.isoformat(),
    )
    assert decision_for(session, body) is not None


# --- rules, end to end ------------------------------------------------------
def test_an_unrated_target_is_blocked_at_ingest(
    client, post_event, session, rated_agents
):
    body = post_event(
        actor_id="payment-agent",
        target_id="brand-new-agent",
        target_type="agent",
        action_type="agent_message",
        timestamp=LATER.isoformat(),
    )
    decision = decision_for(session, body)
    assert decision.decision == "blocked"
    assert decision.rule == "unrated_counterparty"
    assert decision.target_trust == "unrated"


def test_an_operator_assertion_changes_the_verdict(
    client, post_event, session, rated_agents
):
    """The lever works: classify the counterparty, and the answer changes."""
    first = post_event(
        actor_id="payment-agent",
        target_id="partner-agent",
        target_type="agent",
        action_type="agent_message",
        timestamp=LATER.isoformat(),
    )
    assert decision_for(session, first).rule == "unrated_counterparty"

    client.post("/agents/partner-agent/trust", json={"trust_level": "external_trusted"})

    second = post_event(
        actor_id="payment-agent",
        target_id="partner-agent",
        target_type="agent",
        action_type="agent_message",
        timestamp=(LATER + timedelta(minutes=5)).isoformat(),
    )
    decision = decision_for(session, second)
    assert decision.decision == "allowed"
    assert decision.target_trust == "external_trusted"


def test_an_untrusted_requester_is_blocked_at_ingest(
    client, post_event, session, rated_agents
):
    client.post("/agents/rogue-agent/trust", json={"trust_level": "external_untrusted"})
    for i in range(4):
        post_event(
            actor_id="rogue-agent",
            target_id="bank-api",
            timestamp=(BASE_TIME + timedelta(minutes=i)).isoformat(),
        )

    body = post_event(
        actor_id="rogue-agent",
        target_id="payment-agent",
        target_type="agent",
        action_type="agent_message",
        timestamp=LATER.isoformat(),
    )
    decision = decision_for(session, body)
    assert decision.decision == "blocked"
    assert decision.rule == "untrusted_party"
    assert decision.requester_trust == "external_untrusted"


def test_internal_to_internal_is_allowed_from_the_first_contact(
    client, post_event, session
):
    """Onboarding removes the cold-start refusal that would otherwise apply."""
    client.post("/agents/agent-a/trust", json={"trust_level": "internal"})
    client.post("/agents/agent-b/trust", json={"trust_level": "internal"})

    body = post_event(
        actor_id="agent-a",
        target_id="agent-b",
        target_type="agent",
        action_type="agent_message",
        timestamp=LATER.isoformat(),
    )
    decision = decision_for(session, body)
    assert decision.decision == "allowed"
    assert decision.rule == "default_allow"
    assert (decision.requester_trust, decision.target_trust) == ("internal", "internal")


# --- immutability and shape -------------------------------------------------
def test_the_verdict_snapshots_trust_at_decision_time(client, post_event, session):
    """A later reclassification must not rewrite why something was decided."""
    client.post("/agents/agent-a/trust", json={"trust_level": "internal"})
    client.post("/agents/agent-b/trust", json={"trust_level": "internal"})
    body = post_event(
        actor_id="agent-a",
        target_id="agent-b",
        target_type="agent",
        action_type="agent_message",
        timestamp=LATER.isoformat(),
    )

    client.post("/agents/agent-b/trust", json={"trust_level": "external_untrusted"})

    session.expire_all()
    decision = decision_for(session, body)
    assert (
        decision.target_trust == "internal"
    ), "the snapshot must not follow the identity"
    assert decision.decision == "allowed"


def test_one_event_gets_exactly_one_verdict(client, post_event, session, rated_agents):
    body = post_event(
        actor_id="payment-agent",
        target_id="finance-agent",
        target_type="agent",
        action_type="agent_message",
        timestamp=LATER.isoformat(),
    )
    event_id = body["event"]["event_id"]
    assert session.query(A2ADecision).filter_by(event_id=event_id).count() == 1


def test_a_blocked_interaction_does_not_flag_the_event(client, post_event, session):
    """Recording, not enforcing — and deliberately so.

    A flagged event is excluded from baselines, so flagging first contact would
    remove the counterparty from the baseline and make every later contact look
    novel. The verdict is durable without poisoning the data it depends on.
    """
    # The requester has no baseline, so the anomaly rules decline to judge this
    # event at all. A2A does not depend on the requester's rating, so it still
    # evaluates — which isolates its effect from the anomaly layer's.
    body = post_event(
        actor_id="fresh-agent",
        target_id="brand-new-agent",
        target_type="agent",
        action_type="agent_message",
        timestamp=LATER.isoformat(),
    )

    assert decision_for(session, body).decision == "blocked"
    assert body["alerts"] == [], "the anomaly layer should not have judged this event"
    assert body["event"]["platform_status"] == "allowed"
