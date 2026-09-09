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
    assert "unrated_delegate" in decision["reason"]

    verdict = decision["permission_decisions"][0]
    assert verdict["rule"] == "unrated_delegate"
    assert verdict["granted_as"] is None
    assert "unrated" in verdict["reason"]


def test_the_incident_delegation_is_reachable_by_filter(client, banking_incident):
    """An analyst asking 'what was blocked?' must find this without knowing ids."""
    blocked = client.get("/delegations?decision=blocked").json()
    pairs = {(d["delegator_id"], d["delegate_id"]) for d in blocked}
    assert ("payment-agent", "external-agent-x") in pairs

    scoped = client.get("/delegations?delegate_id=external-agent-x").json()
    assert len(scoped) == 1
    assert scoped[0]["decision"] == "blocked"
