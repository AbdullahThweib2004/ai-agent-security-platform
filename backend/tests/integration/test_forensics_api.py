"""GET /forensics/timeline/{event_id}, queried from every point in a chain."""

from __future__ import annotations

from datetime import timedelta

import pytest

from tests.conftest import BASE_TIME

pytestmark = pytest.mark.integration


@pytest.fixture
def chain(post_event):
    """A simple four-level chain with one branch.

    root ── a ── b ── leaf
             └── branch
    """
    root = post_event(
        actor_id="analyst-1",
        actor_type="user",
        target_id="finance-agent",
        target_type="agent",
        action_type="delegation",
        timestamp=BASE_TIME.isoformat(),
    )["event"]
    a = post_event(
        actor_id="finance-agent",
        target_id="payment-agent",
        target_type="agent",
        action_type="delegation",
        parent_event_id=root["event_id"],
        timestamp=(BASE_TIME + timedelta(minutes=1)).isoformat(),
    )["event"]
    b = post_event(
        actor_id="payment-agent",
        target_id="bank-api",
        parent_event_id=a["event_id"],
        timestamp=(BASE_TIME + timedelta(minutes=2)).isoformat(),
    )["event"]
    branch = post_event(
        actor_id="payment-agent",
        target_id="payments-db",
        target_type="database",
        action_type="data_access",
        parent_event_id=a["event_id"],
        timestamp=(BASE_TIME + timedelta(minutes=3)).isoformat(),
    )["event"]
    leaf = post_event(
        actor_id="bank-api",
        actor_type="agent",
        target_id="ledger-tool",
        target_type="tool",
        action_type="tool_call",
        parent_event_id=b["event_id"],
        timestamp=(BASE_TIME + timedelta(minutes=4)).isoformat(),
    )["event"]
    return {"root": root, "a": a, "b": b, "branch": branch, "leaf": leaf}


def relations(timeline):
    return {e["event"]["event_id"]: e["relation"] for e in timeline["entries"]}


def depths(timeline):
    return {e["event"]["event_id"]: e["depth"] for e in timeline["entries"]}


def test_a_lone_event_is_its_own_root(client, post_event):
    event = post_event()["event"]
    timeline = client.get(f"/forensics/timeline/{event['event_id']}").json()
    assert timeline["root_event_id"] == event["event_id"]
    assert timeline["event_count"] == 1
    assert timeline["entries"][0]["relation"] == "self"
    assert timeline["entries"][0]["depth"] == 0


def test_from_the_leaf_walks_back_to_the_root(client, chain):
    timeline = client.get(f"/forensics/timeline/{chain['leaf']['event_id']}").json()

    assert timeline["root_event_id"] == chain["root"]["event_id"]
    assert timeline["event_count"] == 5
    rel = relations(timeline)
    assert rel[chain["leaf"]["event_id"]] == "self"
    assert rel[chain["root"]["event_id"]] == "ancestor"
    assert rel[chain["a"]["event_id"]] == "ancestor"
    assert rel[chain["b"]["event_id"]] == "ancestor"
    # the sibling is on the same incident but not on this event's own path
    assert rel[chain["branch"]["event_id"]] == "related"


def test_from_the_root_everything_below_is_a_descendant(client, chain):
    timeline = client.get(f"/forensics/timeline/{chain['root']['event_id']}").json()
    rel = relations(timeline)
    assert rel[chain["root"]["event_id"]] == "self"
    assert set(rel.values()) == {"self", "descendant"}
    assert timeline["event_count"] == 5


def test_from_mid_chain_both_directions_are_present(client, chain):
    timeline = client.get(f"/forensics/timeline/{chain['b']['event_id']}").json()
    rel = relations(timeline)
    assert rel[chain["b"]["event_id"]] == "self"
    assert rel[chain["root"]["event_id"]] == "ancestor"
    assert rel[chain["a"]["event_id"]] == "ancestor"
    assert rel[chain["leaf"]["event_id"]] == "descendant"
    assert rel[chain["branch"]["event_id"]] == "related"


def test_every_entry_point_reconstructs_the_same_incident(client, chain):
    """Whichever thread you pull, you get the whole tree."""
    seen = set()
    for event in chain.values():
        timeline = client.get(f"/forensics/timeline/{event['event_id']}").json()
        assert timeline["root_event_id"] == chain["root"]["event_id"]
        seen.add(tuple(sorted(depths(timeline).items())))
    assert len(seen) == 1, "depth assignment should not depend on the entry point"


def test_depth_is_measured_from_the_root(client, chain):
    timeline = client.get(f"/forensics/timeline/{chain['leaf']['event_id']}").json()
    d = depths(timeline)
    assert d[chain["root"]["event_id"]] == 0
    assert d[chain["a"]["event_id"]] == 1
    assert d[chain["b"]["event_id"]] == 2
    assert d[chain["branch"]["event_id"]] == 2
    assert d[chain["leaf"]["event_id"]] == 3


def test_entries_are_chronological(client, chain):
    timeline = client.get(f"/forensics/timeline/{chain['leaf']['event_id']}").json()
    stamps = [e["event"]["timestamp"] for e in timeline["entries"]]
    assert stamps == sorted(stamps)


def test_window_and_participants(client, chain):
    timeline = client.get(f"/forensics/timeline/{chain['leaf']['event_id']}").json()
    assert timeline["participants"] == sorted(
        {
            "analyst-1",
            "finance-agent",
            "payment-agent",
            "bank-api",
            "payments-db",
            "ledger-tool",
        }
    )
    assert timeline["started_at"] < timeline["ended_at"]


def test_alerts_ride_along_with_their_event(client, post_event, seed_baseline):
    seed_baseline()
    flagged = post_event(
        actor_id="payment-agent",
        target_id="external-agent-x",
        target_type="agent",
        action_type="delegation",
        permissions_used=["bank:admin"],
        metadata={"amount": 880_000.0},
        timestamp=(BASE_TIME + timedelta(hours=1)).isoformat(),
    )["event"]
    timeline = client.get(f"/forensics/timeline/{flagged['event_id']}").json()
    entry = next(
        e for e in timeline["entries"] if e["event"]["event_id"] == flagged["event_id"]
    )
    assert {a["rule_name"] for a in entry["alerts"]} == {
        "unseen_counterparty",
        "value_excursion",
        "new_permission",
    }
    assert timeline["alert_count"] == 3


def test_unknown_event_is_404(client):
    response = client.get("/forensics/timeline/99999999-9999-9999-9999-999999999999")
    assert response.status_code == 404


def test_malformed_event_id_is_422(client):
    assert client.get("/forensics/timeline/not-a-uuid").status_code == 422
