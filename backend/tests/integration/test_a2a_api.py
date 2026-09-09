"""GET /a2a-decisions and GET /a2a-decisions/{id}, against real Postgres."""

from __future__ import annotations

import re
from datetime import timedelta

import pytest

from tests.conftest import BASE_TIME

pytestmark = pytest.mark.integration

LATER = BASE_TIME + timedelta(hours=2)
UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I
)


@pytest.fixture
def scenario(client, post_event, seed_baseline):
    """Three interactions spanning both outcomes and both blocking rules."""
    seed_baseline(actor_id="payment-agent", permissions=("bank:transfer",))
    client.post("/agents/finance-agent/trust", json={"trust_level": "internal"})
    client.post("/agents/payment-agent/trust", json={"trust_level": "internal"})
    client.post("/agents/rogue-agent/trust", json={"trust_level": "external_untrusted"})

    allowed = post_event(
        actor_id="finance-agent",
        target_id="payment-agent",
        target_type="agent",
        action_type="agent_message",
        permissions_used=["agent:status"],
        timestamp=LATER.isoformat(),
    )
    unrated = post_event(
        actor_id="payment-agent",
        target_id="brand-new-agent",
        target_type="agent",
        action_type="agent_message",
        timestamp=(LATER + timedelta(minutes=1)).isoformat(),
    )
    untrusted = post_event(
        actor_id="finance-agent",
        target_id="rogue-agent",
        target_type="agent",
        action_type="agent_message",
        timestamp=(LATER + timedelta(minutes=2)).isoformat(),
    )
    return {"allowed": allowed, "unrated": unrated, "untrusted": untrusted}


def by_event(rows, event_body):
    event_id = event_body["event"]["event_id"]
    return next(r for r in rows if r["event_id"] == event_id)


# --- list -------------------------------------------------------------------
def test_no_decisions_when_no_agent_to_agent_traffic(client, seed_baseline):
    seed_baseline()
    assert client.get("/a2a-decisions").json() == []


def test_list_returns_every_decision(client, scenario):
    rows = client.get("/a2a-decisions").json()
    assert len(rows) == 3
    assert {r["decision"] for r in rows} == {"allowed", "blocked"}
    for row in rows:
        assert row["decision_id"] and row["event_id"]
        assert row["rule"] and row["reason"]
        assert row["requester_trust"] and row["target_trust"]


def test_list_is_newest_first(client, scenario):
    stamps = [r["decided_at"] for r in client.get("/a2a-decisions").json()]
    assert stamps == sorted(stamps, reverse=True)


def test_filter_by_requester(client, scenario):
    rows = client.get("/a2a-decisions?requester_id=finance-agent").json()
    assert len(rows) == 2
    assert {r["requester_id"] for r in rows} == {"finance-agent"}


def test_filter_by_target(client, scenario):
    rows = client.get("/a2a-decisions?target_id=brand-new-agent").json()
    assert len(rows) == 1
    assert rows[0]["rule"] == "unrated_counterparty"


@pytest.mark.parametrize("value, expected", [("allowed", 1), ("blocked", 2)])
def test_filter_by_each_decision(client, scenario, value, expected):
    rows = client.get(f"/a2a-decisions?decision={value}").json()
    assert len(rows) == expected
    assert {r["decision"] for r in rows} == {value}


def test_filters_compose(client, scenario):
    rows = client.get(
        "/a2a-decisions?requester_id=finance-agent&target_id=rogue-agent&decision=blocked"
    ).json()
    assert len(rows) == 1
    assert rows[0]["rule"] == "untrusted_party"


def test_a_valid_filter_matching_nothing_is_an_empty_list(client, scenario):
    """Agent ids are open-ended, so an unknown one is a legitimate empty result."""
    assert client.get("/a2a-decisions?requester_id=nobody").json() == []
    assert client.get("/a2a-decisions?target_id=nobody").json() == []
    assert (
        client.get("/a2a-decisions?requester_id=finance-agent&target_id=nobody").json()
        == []
    )


@pytest.mark.parametrize("value", ["apocalyptic", "ALLOWED", "limited", "denied", ""])
def test_an_invalid_decision_is_422_not_an_empty_list(client, scenario, value):
    """`limited` included deliberately: it is a delegation outcome, not an A2A one."""
    response = client.get(f"/a2a-decisions?decision={value}")
    assert response.status_code == 422
    assert "Input should be" in response.json()["detail"][0]["msg"]


def test_pagination(client, scenario):
    page = client.get("/a2a-decisions?limit=2").json()
    assert len(page) == 2
    rest = client.get("/a2a-decisions?limit=2&offset=2").json()
    assert len(rest) == 1
    assert rest[0]["decision_id"] not in {r["decision_id"] for r in page}


@pytest.mark.parametrize(
    "path",
    [
        "/a2a-decisions?limit=0",
        "/a2a-decisions?limit=99999",
        "/a2a-decisions?offset=-1",
    ],
)
def test_out_of_range_paging_is_422(client, path):
    assert client.get(path).status_code == 422


# --- detail -----------------------------------------------------------------
def test_detail_carries_the_trust_snapshots(client, scenario):
    listed = by_event(client.get("/a2a-decisions").json(), scenario["untrusted"])
    detail = client.get(f"/a2a-decisions/{listed['decision_id']}").json()

    assert detail["decision"] == "blocked"
    assert detail["rule"] == "untrusted_party"
    assert detail["requester_trust"] == "internal"
    assert detail["target_trust"] == "external_untrusted"
    assert "regardless of what was requested" in detail["reason"]


def test_detail_matches_the_listed_row(client, scenario):
    listed = client.get("/a2a-decisions").json()[0]
    detail = client.get(f"/a2a-decisions/{listed['decision_id']}").json()
    assert detail == listed


def test_the_snapshot_does_not_follow_a_later_reclassification(client, scenario):
    listed = by_event(client.get("/a2a-decisions").json(), scenario["untrusted"])
    client.post("/agents/rogue-agent/trust", json={"trust_level": "internal"})

    detail = client.get(f"/a2a-decisions/{listed['decision_id']}").json()
    assert detail["target_trust"] == "external_untrusted"
    assert detail["decision"] == "blocked"


def test_unknown_decision_is_404(client):
    response = client.get("/a2a-decisions/99999999-9999-9999-9999-999999999999")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"]


def test_malformed_decision_id_is_422(client):
    assert client.get("/a2a-decisions/not-a-uuid").status_code == 422


# --- cross-reference with the delegation record ------------------------------
def test_the_id_a_delegation_cites_resolves_through_this_endpoint(
    client, post_event, seed_baseline
):
    """The two APIs must actually join up.

    A delegation refused upstream names the a2a decision in its reason. That id
    has to be fetchable here, or the explanation is a dead end for whoever reads
    it.
    """
    seed_baseline(
        actor_id="finance-agent",
        target_id="fx-rate-tool",
        target_type="tool",
        permissions=("fx:read",),
    )
    handoff = post_event(
        actor_id="finance-agent",
        target_id="brand-new-agent",
        target_type="agent",
        action_type="delegation",
        permissions_used=[],
        metadata={"requested_permissions": ["fx:read"]},
        timestamp=LATER.isoformat(),
    )
    event_id = handoff["event"]["event_id"]

    delegation = next(
        d for d in client.get("/delegations").json() if d["event_id"] == event_id
    )
    assert delegation["decision"] == "blocked"
    assert "refused upstream by interaction policy" in delegation["reason"]

    cited = UUID_RE.search(delegation["reason"])
    assert cited, f"no decision id in the reason: {delegation['reason']!r}"

    response = client.get(f"/a2a-decisions/{cited.group(0)}")
    assert response.status_code == 200, "the cited id must resolve"
    decision = response.json()

    # and it must be the verdict for the same event, not merely a valid id
    assert decision["event_id"] == event_id
    assert decision["decision"] == "blocked"
    assert decision["rule"] == "unrated_counterparty"
    assert decision["target_id"] == "brand-new-agent"
