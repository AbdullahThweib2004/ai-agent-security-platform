"""GET /delegations and GET /delegations/{id}, against real Postgres."""

from __future__ import annotations

from datetime import timedelta

import pytest

from tests.conftest import BASE_TIME

pytestmark = pytest.mark.integration

LATER = BASE_TIME + timedelta(hours=2)


@pytest.fixture
def scenario(post_event, seed_baseline):
    """Three delegations spanning all three outcomes.

    Built through the API so the policy engine really runs; nothing here is
    inserted straight into the table.
    """
    # finance-agent earns a history, and with it the permissions it may pass on.
    seed_baseline(
        actor_id="finance-agent",
        target_id="fx-rate-tool",
        target_type="tool",
        permissions=("fx:read", "bank:transfer", "bank:read"),
    )
    # payment-agent earns enough history to be a rated delegate.
    seed_baseline(actor_id="payment-agent", permissions=("bank:transfer",))

    allowed = post_event(
        actor_id="finance-agent",
        target_id="payment-agent",
        target_type="agent",
        action_type="delegation",
        permissions_used=[],
        metadata={"requested_permissions": ["fx:read"]},
        timestamp=LATER.isoformat(),
    )
    limited = post_event(
        actor_id="finance-agent",
        target_id="payment-agent",
        target_type="agent",
        action_type="delegation",
        permissions_used=[],
        metadata={"requested_permissions": ["fx:read", "bank:transfer"]},
        timestamp=(LATER + timedelta(minutes=1)).isoformat(),
    )
    blocked = post_event(
        actor_id="finance-agent",
        target_id="external-agent-x",
        target_type="agent",
        action_type="delegation",
        permissions_used=[],
        metadata={"requested_permissions": ["fx:read"]},
        timestamp=(LATER + timedelta(minutes=2)).isoformat(),
    )
    return {"allowed": allowed, "limited": limited, "blocked": blocked}


def by_event(delegations, event_body):
    event_id = event_body["event"]["event_id"]
    return next(d for d in delegations if d["event_id"] == event_id)


# --- list -------------------------------------------------------------------
def test_no_delegations_when_none_have_happened(client, seed_baseline):
    seed_baseline()
    assert client.get("/delegations").json() == []


def test_list_returns_every_decision(client, scenario):
    body = client.get("/delegations").json()
    assert len(body) == 3
    assert {d["decision"] for d in body} == {"allowed", "limited", "blocked"}
    for delegation in body:
        assert delegation["delegation_id"]
        assert delegation["event_id"]
        assert delegation["reason"]
        assert delegation["decided_at"]


def test_list_is_newest_first(client, scenario):
    stamps = [d["decided_at"] for d in client.get("/delegations").json()]
    assert stamps == sorted(stamps, reverse=True)


def test_only_delegation_events_produce_rows(client, scenario, post_event):
    """An api_call is not a handoff and must not create a decision."""
    before = len(client.get("/delegations").json())
    post_event(
        actor_id="finance-agent",
        timestamp=(LATER + timedelta(minutes=30)).isoformat(),
    )
    assert len(client.get("/delegations").json()) == before


def test_filter_by_delegator(client, scenario):
    rows = client.get("/delegations?delegator_id=finance-agent").json()
    assert len(rows) == 3
    assert {r["delegator_id"] for r in rows} == {"finance-agent"}
    assert client.get("/delegations?delegator_id=payment-agent").json() == []


def test_filter_by_delegate(client, scenario):
    rows = client.get("/delegations?delegate_id=payment-agent").json()
    assert len(rows) == 2
    assert {r["delegate_id"] for r in rows} == {"payment-agent"}


@pytest.mark.parametrize("decision", ["allowed", "limited", "blocked"])
def test_filter_by_each_decision(client, scenario, decision):
    rows = client.get(f"/delegations?decision={decision}").json()
    assert len(rows) == 1
    assert rows[0]["decision"] == decision


def test_filters_compose(client, scenario):
    rows = client.get(
        "/delegations?delegator_id=finance-agent&delegate_id=payment-agent&decision=limited"
    ).json()
    assert len(rows) == 1
    assert rows[0]["decision"] == "limited"
    assert rows[0]["delegate_id"] == "payment-agent"


def test_a_valid_filter_matching_nothing_is_an_empty_list(client, scenario):
    """Agent ids are open-ended, so an unknown one is a legitimate empty result."""
    assert client.get("/delegations?delegate_id=nobody").json() == []
    assert client.get("/delegations?delegator_id=nobody").json() == []


@pytest.mark.parametrize("value", ["apocalyptic", "ALLOWED", "denied", "limited "])
def test_an_invalid_decision_is_422_not_an_empty_list(client, scenario, value):
    """An empty list would be indistinguishable from 'nothing was blocked'."""
    response = client.get(f"/delegations?decision={value}")
    assert response.status_code == 422
    assert "Input should be" in response.json()["detail"][0]["msg"]


def test_pagination(client, scenario):
    page = client.get("/delegations?limit=2").json()
    assert len(page) == 2
    rest = client.get("/delegations?limit=2&offset=2").json()
    assert len(rest) == 1
    assert rest[0]["delegation_id"] not in {d["delegation_id"] for d in page}


@pytest.mark.parametrize(
    "path",
    ["/delegations?limit=0", "/delegations?limit=99999", "/delegations?offset=-1"],
)
def test_out_of_range_paging_is_422(client, path):
    assert client.get(path).status_code == 422


# --- detail -----------------------------------------------------------------
def test_detail_carries_the_per_permission_reasoning(client, scenario):
    listed = by_event(client.get("/delegations").json(), scenario["limited"])
    detail = client.get(f"/delegations/{listed['delegation_id']}").json()

    assert detail["decision"] == "limited"
    assert detail["requested_permissions"] == ["fx:read", "bank:transfer"]
    assert detail["granted_permissions"] == ["fx:read", "bank:read"]

    verdicts = {v["permission"]: v for v in detail["permission_decisions"]}
    assert verdicts["fx:read"]["decision"] == "allowed"
    assert verdicts["fx:read"]["granted_as"] == "fx:read"

    reduced = verdicts["bank:transfer"]
    assert reduced["decision"] == "limited"
    assert reduced["rule"] == "sensitive_category"
    assert reduced["granted_as"] == "bank:read"
    assert "money movement" in reduced["reason"]


def test_detail_matches_the_listed_row(client, scenario):
    listed = client.get("/delegations").json()[0]
    detail = client.get(f"/delegations/{listed['delegation_id']}").json()
    assert detail == listed


def test_unknown_delegation_is_404(client):
    response = client.get("/delegations/99999999-9999-9999-9999-999999999999")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"]


def test_malformed_delegation_id_is_422(client):
    assert client.get("/delegations/not-a-uuid").status_code == 422


def test_every_granted_permission_appears_in_a_verdict(client, scenario):
    """The summary and the per-permission detail must not disagree."""
    for row in client.get("/delegations").json():
        granted_in_verdicts = {
            v["granted_as"] for v in row["permission_decisions"] if v["granted_as"]
        }
        assert set(row["granted_permissions"]) == granted_in_verdicts
        assert {v["permission"] for v in row["permission_decisions"]} == set(
            row["requested_permissions"]
        )


# --- the two ways a delegation can declare what it wants --------------------
def test_the_explicit_requested_permissions_path_is_honoured_end_to_end(
    client, post_event, seed_baseline
):
    """`requested_permissions` governs, not `permissions_used`.

    These are different facts: what the delegator is exercising, and what the
    delegate is asking to receive. This makes them disagree on purpose, so a
    regression that silently read the wrong one cannot pass.
    """
    seed_baseline(
        actor_id="finance-agent",
        target_id="fx-rate-tool",
        target_type="tool",
        permissions=("fx:read", "ledger:reconcile"),
    )
    seed_baseline(actor_id="payment-agent", permissions=("bank:transfer",))

    handoff = post_event(
        actor_id="finance-agent",
        target_id="payment-agent",
        target_type="agent",
        action_type="delegation",
        permissions_used=["ledger:reconcile"],  # what it is exercising
        metadata={"requested_permissions": ["fx:read"]},  # what it is asking for
        timestamp=LATER.isoformat(),
    )
    decision = by_event(client.get("/delegations").json(), handoff)

    assert decision["requested_permissions"] == ["fx:read"]
    assert "ledger:reconcile" not in decision["requested_permissions"]
    assert decision["granted_permissions"] == ["fx:read"]


def test_the_fallback_path_still_governs_legacy_emitters(
    client, post_event, seed_baseline
):
    """An emitter predating this feature is still judged, with no backfill."""
    seed_baseline(
        actor_id="finance-agent",
        target_id="fx-rate-tool",
        target_type="tool",
        permissions=("fx:read",),
    )
    seed_baseline(actor_id="payment-agent", permissions=("bank:transfer",))

    handoff = post_event(
        actor_id="finance-agent",
        target_id="payment-agent",
        target_type="agent",
        action_type="delegation",
        permissions_used=["fx:read"],
        metadata={"invoice": "INV-1"},  # no requested_permissions at all
        timestamp=LATER.isoformat(),
    )
    decision = by_event(client.get("/delegations").json(), handoff)

    assert decision["requested_permissions"] == ["fx:read"]
    assert decision["decision"] == "allowed"
