"""Regression test: the banking incident, exactly as validated by hand.

This encodes the trace that was walked manually during development — the one
that proved forensics recovers a root cause four hops up from a leaf *and*
surfaces the sibling customer-db branch that is not on the direct ancestor path.
If any of that silently changes, this fails.

    analyst-1  --delegation-->  finance-agent                     (root, depth 0)
    finance-agent --delegation--> payment-agent                   (depth 1, value_excursion)
    payment-agent --delegation--> external-agent-x                (depth 2, three rules)
    payment-agent --data_access--> customer-db                    (depth 2, sibling branch)
    external-agent-x --api_call--> offshore-api                   (depth 3, the payout leaves)
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from tests.conftest import BASE_TIME

pytestmark = pytest.mark.integration

NORMAL_INVOICES = [11_800.0, 2_400.0, 9_600.0, 4_100.0, 7_250.0]
INCIDENT_AMOUNT = 880_000.0


@pytest.fixture
def banking_incident(post_event):
    """Ordinary operations, then the incident chained onto a legitimate request."""
    # --- two weeks of normal invoice runs, in miniature ---------------------
    for day, amount in enumerate(NORMAL_INVOICES):
        at = BASE_TIME + timedelta(days=day)
        request = post_event(
            actor_type="user",
            actor_id="analyst-1",
            target_type="agent",
            target_id="finance-agent",
            action_type="delegation",
            permissions_used=["finance:request_payment"],
            timestamp=at.isoformat(),
        )["event"]
        approve = post_event(
            actor_id="finance-agent",
            target_type="agent",
            target_id="payment-agent",
            action_type="delegation",
            permissions_used=["payments:initiate"],
            metadata={"amount": amount},
            parent_event_id=request["event_id"],
            timestamp=(at + timedelta(minutes=1)).isoformat(),
        )["event"]
        transfer = post_event(
            actor_id="payment-agent",
            target_type="api",
            target_id="bank-api",
            action_type="api_call",
            permissions_used=["bank:transfer"],
            metadata={"amount": amount},
            parent_event_id=approve["event_id"],
            timestamp=(at + timedelta(minutes=2)).isoformat(),
        )["event"]
        post_event(
            actor_id="payment-agent",
            target_type="database",
            target_id="payments-db",
            action_type="data_access",
            permissions_used=["db:write_payment"],
            metadata={"rows": 1},
            parent_event_id=transfer["event_id"],
            timestamp=(at + timedelta(minutes=3)).isoformat(),
        )

    # --- the incident -------------------------------------------------------
    at = BASE_TIME + timedelta(days=len(NORMAL_INVOICES))
    root = post_event(
        actor_type="user",
        actor_id="analyst-1",
        target_type="agent",
        target_id="finance-agent",
        action_type="delegation",
        permissions_used=["finance:request_payment"],
        timestamp=at.isoformat(),
    )
    approve = post_event(
        actor_id="finance-agent",
        target_type="agent",
        target_id="payment-agent",
        action_type="delegation",
        permissions_used=["payments:initiate"],
        metadata={"amount": INCIDENT_AMOUNT},
        parent_event_id=root["event"]["event_id"],
        timestamp=(at + timedelta(minutes=1)).isoformat(),
    )
    handoff = post_event(
        actor_id="payment-agent",
        target_type="agent",
        target_id="external-agent-x",
        action_type="delegation",
        permissions_used=["bank:transfer", "bank:admin"],
        metadata={"amount": INCIDENT_AMOUNT},
        parent_event_id=approve["event"]["event_id"],
        timestamp=(at + timedelta(minutes=2)).isoformat(),
    )
    exfil_db = post_event(
        actor_id="payment-agent",
        target_type="database",
        target_id="customer-db",
        action_type="data_access",
        permissions_used=["db:read_pii"],
        metadata={"rows": 48_500},
        parent_event_id=approve["event"]["event_id"],
        timestamp=(at + timedelta(minutes=3)).isoformat(),
    )
    payout = post_event(
        actor_id="external-agent-x",
        target_type="api",
        target_id="offshore-api",
        action_type="api_call",
        permissions_used=["net:egress"],
        metadata={"amount": INCIDENT_AMOUNT},
        parent_event_id=handoff["event"]["event_id"],
        timestamp=(at + timedelta(minutes=4)).isoformat(),
    )
    return {
        "root": root,
        "approve": approve,
        "handoff": handoff,
        "exfil_db": exfil_db,
        "payout": payout,
    }


def test_normal_operations_raise_no_alerts(client, post_event):
    """The 20 ordinary events must stay quiet, or the incident drowns in noise."""
    for day, amount in enumerate(NORMAL_INVOICES):
        at = BASE_TIME + timedelta(days=day)
        body = post_event(
            actor_id="finance-agent",
            target_type="agent",
            target_id="payment-agent",
            action_type="delegation",
            permissions_used=["payments:initiate"],
            metadata={"amount": amount},
            timestamp=at.isoformat(),
        )
        assert (
            body["alerts"] == []
        ), f"day {day} (amount {amount}) raised {body['alerts']}"


def test_the_incident_raises_exactly_the_expected_alerts(banking_incident):
    assert {a["rule_name"] for a in banking_incident["approve"]["alerts"]} == {
        "value_excursion"
    }
    assert {a["rule_name"] for a in banking_incident["handoff"]["alerts"]} == {
        "unseen_counterparty",
        "value_excursion",
        "new_permission",
    }
    assert {a["rule_name"] for a in banking_incident["exfil_db"]["alerts"]} == {
        "unseen_counterparty",
        "new_permission",
    }
    # external-agent-x has no history, so its own payout cannot be judged
    assert banking_incident["payout"]["alerts"] == []


def test_root_cause_recovered_four_hops_up_from_the_payout(client, banking_incident):
    """The headline capability: hand it a leaf, get the whole incident back."""
    payout_id = banking_incident["payout"]["event"]["event_id"]
    timeline = client.get(f"/forensics/timeline/{payout_id}").json()

    assert timeline["root_event_id"] == banking_incident["root"]["event"]["event_id"]
    assert timeline["event_count"] == 5
    assert timeline["alert_count"] == 6

    by_id = {e["event"]["event_id"]: e for e in timeline["entries"]}
    root = by_id[banking_incident["root"]["event"]["event_id"]]
    approve = by_id[banking_incident["approve"]["event"]["event_id"]]
    handoff = by_id[banking_incident["handoff"]["event"]["event_id"]]
    sibling = by_id[banking_incident["exfil_db"]["event"]["event_id"]]
    payout = by_id[payout_id]

    assert (root["relation"], root["depth"]) == ("ancestor", 0)
    assert (approve["relation"], approve["depth"]) == ("ancestor", 1)
    assert (handoff["relation"], handoff["depth"]) == ("ancestor", 2)
    assert (payout["relation"], payout["depth"]) == ("self", 3)

    # The branch that is NOT on the payout's ancestor path still shows up —
    # this is the part a naive parent-walk would miss.
    assert (sibling["relation"], sibling["depth"]) == ("related", 2)
    assert sibling["event"]["target_id"] == "customer-db"
    assert {a["rule_name"] for a in sibling["alerts"]} == {
        "unseen_counterparty",
        "new_permission",
    }

    assert timeline["participants"] == sorted(
        {
            "analyst-1",
            "finance-agent",
            "payment-agent",
            "external-agent-x",
            "customer-db",
            "offshore-api",
        }
    )


def test_the_incident_surfaces_in_the_graph(client, banking_incident):
    graph = client.get("/graph").json()
    nodes = {n["id"]: n for n in graph["nodes"]}
    edges = {(e["source"], e["target"]): e for e in graph["edges"]}

    assert nodes["payment-agent"]["health"] == "suspicious"
    assert nodes["finance-agent"]["health"] == "suspicious"
    assert nodes["external-agent-x"]["health"] == "unrated"
    assert edges[("payment-agent", "external-agent-x")]["suspicious"] is True
    assert edges[("payment-agent", "customer-db")]["suspicious"] is True
    assert edges[("payment-agent", "bank-api")]["suspicious"] is False


def test_the_flagged_payment_never_widens_the_baseline(client, banking_incident):
    """After the incident, 'normal' for payment-agent is still the old range."""
    baseline = client.get("/graph/payment-agent").json()["baseline"]
    assert baseline["typical_value_range"]["max"] == max(NORMAL_INVOICES)
    assert baseline["usual_agents_contacted"] == []
    assert "bank:admin" not in baseline["usual_permissions"]
    assert "db:read_pii" not in baseline["usual_permissions"]


# --- delegation security: the same incident, judged as a handoff ------------
def delegation_for(client, event_body):
    event_id = event_body["event"]["event_id"]
    rows = [d for d in client.get("/delegations").json() if d["event_id"] == event_id]
    assert len(rows) == 1, f"expected one delegation decision for {event_id}"
    return rows[0]


def test_the_incident_handoff_is_refused_by_delegation_policy(client, banking_incident):
    """Pinned: payment-agent -> external-agent-x is BLOCKED, permission by permission.

    This is the handoff at the centre of the incident — the payout leaving for an
    agent nobody has ever seen. The anomaly rules already flag the event; this
    asserts the authority itself never travelled with it.
    """
    decision = delegation_for(client, banking_incident["handoff"])

    assert decision["delegator_id"] == "payment-agent"
    assert decision["delegate_id"] == "external-agent-x"
    assert decision["decision"] == "blocked"
    assert decision["requested_permissions"] == ["bank:transfer", "bank:admin"]
    assert decision["granted_permissions"] == []

    verdicts = {v["permission"]: v for v in decision["permission_decisions"]}
    assert set(verdicts) == {"bank:transfer", "bank:admin"}

    # payment-agent does hold bank:transfer, so confinement passes; it is money
    # movement, so the reduced form bank:read would be granted instead — but
    # payment-agent does not hold that either, so nothing is granted.
    transfer = verdicts["bank:transfer"]
    assert transfer["decision"] == "blocked"
    assert transfer["rule"] == "sensitive_category"
    assert transfer["granted_as"] is None
    assert "money movement" in transfer["reason"]
    assert "does not hold that either" in transfer["reason"]

    # payment-agent has never held bank:admin at all.
    admin = verdicts["bank:admin"]
    assert admin["decision"] == "blocked"
    assert admin["rule"] == "confinement"
    assert admin["granted_as"] is None
    assert "never held" in admin["reason"]


def test_the_legitimate_handoff_in_the_same_chain_is_still_allowed(
    client, banking_incident
):
    """Least privilege must not break the normal path it sits on."""
    decision = delegation_for(client, banking_incident["approve"])
    assert decision["delegator_id"] == "finance-agent"
    assert decision["delegate_id"] == "payment-agent"
    assert decision["decision"] == "allowed"
    assert decision["granted_permissions"] == ["payments:initiate"]
    assert decision["permission_decisions"][0]["rule"] == "default_allow"


def test_a_handoff_to_an_unrated_agent_is_refused_outright(client, post_event):
    """Pinned: the cold-start block, on a permission the delegator plainly holds.

    finance-agent holds fx:read and fx:read is not sensitive, so only one thing
    can refuse this — the delegate having no history to vouch for it.
    """
    for i in range(4):
        post_event(
            actor_id="finance-agent",
            target_id="fx-rate-tool",
            target_type="tool",
            action_type="tool_call",
            permissions_used=["fx:read"],
            timestamp=(BASE_TIME + timedelta(minutes=i)).isoformat(),
        )
    handoff = post_event(
        actor_id="finance-agent",
        target_id="brand-new-helper",
        target_type="agent",
        action_type="delegation",
        permissions_used=[],
        metadata={"requested_permissions": ["fx:read"]},
        timestamp=(BASE_TIME + timedelta(hours=1)).isoformat(),
    )

    decision = delegation_for(client, handoff)
    assert decision["decision"] == "blocked"
    assert decision["requested_permissions"] == ["fx:read"]
    assert decision["granted_permissions"] == []

    # Since Phase 4 this is refused one layer earlier: interaction policy
    # declines the conversation, and delegation cites that decision instead of
    # re-deriving the same "counterparty is unrated" finding under its own rule
    # name. The outcome is unchanged; the attribution is no longer duplicated.
    assert "refused upstream by interaction policy" in decision["reason"]
    verdict = decision["permission_decisions"][0]
    assert verdict["rule"] == "upstream_a2a_block"
    assert verdict["granted_as"] is None
    assert "unrated" in verdict["reason"], "the upstream reason still explains why"


def test_unrated_delegate_still_fires_where_a2a_does_not_reach(client, post_event):
    """The rule is not dead: A2A covers agent -> agent, this is user -> agent.

    A person delegating to an agent nobody can vouch for is still refused, by
    delegation's own rule, because no interaction verdict exists to cite.
    """
    for i in range(4):
        post_event(
            actor_type="user",
            actor_id="analyst-1",
            target_id="fx-rate-tool",
            target_type="tool",
            action_type="tool_call",
            permissions_used=["fx:read"],
            timestamp=(BASE_TIME + timedelta(minutes=i)).isoformat(),
        )
    handoff = post_event(
        actor_type="user",
        actor_id="analyst-1",
        target_id="brand-new-helper",
        target_type="agent",
        action_type="delegation",
        permissions_used=[],
        metadata={"requested_permissions": ["fx:read"]},
        timestamp=(BASE_TIME + timedelta(hours=1)).isoformat(),
    )

    decision = delegation_for(client, handoff)
    assert decision["decision"] == "blocked"
    assert "unrated_delegate" in decision["reason"]
    assert decision["permission_decisions"][0]["rule"] == "unrated_delegate"


def test_the_incident_delegation_is_reachable_by_filter(client, banking_incident):
    """An analyst asking 'what was blocked?' must find this without knowing ids."""
    blocked = client.get("/delegations?decision=blocked").json()
    pairs = {(d["delegator_id"], d["delegate_id"]) for d in blocked}
    assert ("payment-agent", "external-agent-x") in pairs

    scoped = client.get("/delegations?delegate_id=external-agent-x").json()
    assert len(scoped) == 1
    assert scoped[0]["decision"] == "blocked"


# --- A2A: the interaction gate, before anything is delegated ----------------
def a2a_for(session, event_body):
    from app.models.a2a import A2ADecision

    event_id = event_body["event"]["event_id"]
    return session.query(A2ADecision).filter_by(event_id=event_id).one_or_none()


def test_the_incident_handoff_is_also_refused_at_the_interaction_layer(
    client, session, banking_incident
):
    """Pinned: payment-agent -> external-agent-x is blocked before delegation.

    Two independent layers refuse this handoff for different reasons. Delegation
    refuses the authority; A2A refuses the conversation. This pins the second.
    """
    decision = a2a_for(session, banking_incident["handoff"])

    assert decision is not None
    assert decision.requester_id == "payment-agent"
    assert decision.target_id == "external-agent-x"
    assert decision.decision == "blocked"
    assert decision.rule == "unrated_counterparty"
    assert decision.target_trust == "unrated"
    assert "external-agent-x is unrated" in decision.reason
    assert "too little history to accept an interaction with" in decision.reason


def test_the_legitimate_handoff_is_allowed_at_the_interaction_layer(
    client, session, banking_incident
):
    """The gate must not refuse the ordinary path it sits on."""
    decision = a2a_for(session, banking_incident["approve"])
    assert decision.decision == "allowed"
    assert decision.rule == "default_allow"
    assert decision.requester_id == "finance-agent"
    assert decision.target_id == "payment-agent"


def test_an_operator_assertion_stops_the_next_attempt_at_the_door(
    client, post_event, session, banking_incident
):
    """The lever: after the incident, classify it and the next probe is refused.

    Blocked by identity rather than by looking anomalous all over again — which
    is the whole point of separating trust from behaviour.
    """
    client.post(
        "/agents/external-agent-x/trust", json={"trust_level": "external_untrusted"}
    )
    probe = post_event(
        actor_id="external-agent-x",
        target_id="payment-agent",
        target_type="agent",
        action_type="agent_message",
        permissions_used=["agent:status"],
        timestamp=(
            BASE_TIME + timedelta(days=len(NORMAL_INVOICES), hours=11)
        ).isoformat(),
    )

    decision = a2a_for(session, probe)
    assert decision.decision == "blocked"
    assert decision.rule == "untrusted_party"
    assert decision.requester_trust == "external_untrusted"
    assert "regardless of what was requested" in decision.reason


def test_user_directed_delegations_are_not_agent_to_agent(
    client, session, banking_incident
):
    """analyst-1 is a person. A person directing an agent is not A2A traffic."""
    assert a2a_for(session, banking_incident["root"]) is None


# --- containment: the incident scenario, end to end -------------------------
def test_the_incident_contains_payment_agent(client, session, banking_incident):
    """Pinned: the scenario crosses the threshold and the agent is suspended."""
    from app.models.incident import Incident
    from app.services.identity import trust_level_of
    from app.services.incidents import is_suspended

    incident = session.query(Incident).filter_by(agent_id="payment-agent").one_or_none()
    assert incident is not None, "the attacker should have been contained"
    assert incident.status == "open"
    assert incident.severity == "high"

    # Dated to when the agent acted, not to when the platform noticed.
    assert incident.opened_at < incident.detected_at

    layers = {e.layer for e in incident.evidence}
    assert layers >= {"alert"}, f"expected alert evidence, got {layers}"

    assert is_suspended(session, "payment-agent") is True
    assert trust_level_of(session, "payment-agent") == "external_untrusted"


def test_only_the_attacker_is_contained(client, session, banking_incident):
    """No false containment of the humans or the healthy agents.

    A count-based threshold opened incidents on analyst-1 — a person whose
    delegation was refused by confinement during ordinary work — and on
    finance-agent's cold-start blocks. Corroboration across layers and events
    is what separates them.
    """
    from app.models.incident import Incident

    contained = {i.agent_id for i in session.query(Incident).all()}
    assert contained == {"payment-agent"}
    for innocent in ("analyst-1", "finance-agent", "reconciliation-agent"):
        assert innocent not in contained


def test_subsequent_events_are_recorded_and_marked_not_dropped(
    client, post_event, session, banking_incident
):
    from app.models.event import AgentEvent

    before = session.query(AgentEvent).filter_by(actor_id="payment-agent").count()
    body = post_event(
        actor_id="payment-agent",
        target_id="bank-api",
        target_type="api",
        action_type="api_call",
        permissions_used=["bank:transfer"],
        metadata={"amount": 25.0},
        timestamp=(
            BASE_TIME + timedelta(days=len(NORMAL_INVOICES), hours=12)
        ).isoformat(),
    )
    after = session.query(AgentEvent).filter_by(actor_id="payment-agent").count()

    assert after == before + 1, "containment must not create a blind spot"
    assert body["event"]["actor_suspended"] is True


# --- forensics now shows every layer's conclusion ---------------------------
def test_the_timeline_shows_what_every_layer_concluded(client, banking_incident):
    """Previously a timeline carried alerts only — about a third of the picture."""
    handoff_id = banking_incident["handoff"]["event"]["event_id"]
    timeline = client.get(f"/forensics/timeline/{handoff_id}").json()

    entry = next(e for e in timeline["entries"] if e["event"]["event_id"] == handoff_id)

    assert len(entry["alerts"]) == 3
    assert entry["delegation"] is not None
    assert entry["delegation"]["decision"] == "blocked"
    assert entry["a2a_decision"] is not None
    assert entry["a2a_decision"]["decision"] == "blocked"
    assert entry["a2a_decision"]["rule"] == "unrated_counterparty"

    assert entry["incidents"], "the event that opened the incident should say so"
    incident_ref = entry["incidents"][0]
    assert incident_ref["agent_id"] == "payment-agent"
    assert incident_ref["status"] == "open"
    assert incident_ref["layer"] in {"alert", "delegation", "a2a"}


def test_entries_with_no_verdicts_carry_nulls_not_noise(client, banking_incident):
    root_id = banking_incident["root"]["event"]["event_id"]
    timeline = client.get(f"/forensics/timeline/{root_id}").json()
    entry = next(e for e in timeline["entries"] if e["event"]["event_id"] == root_id)

    # analyst-1 -> finance-agent is a delegation, so it has one of those...
    assert entry["delegation"] is not None
    # ...but it is user -> agent, so interaction policy never judged it.
    assert entry["a2a_decision"] is None
    assert entry["incidents"] == []
