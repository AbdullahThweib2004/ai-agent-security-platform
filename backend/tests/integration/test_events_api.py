"""POST /events and the retrieval routes, against real Postgres and Neo4j."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from tests.conftest import BASE_TIME, event_payload

pytestmark = pytest.mark.integration


def test_ingest_happy_path_writes_both_stores(client, post_event, neo, session):
    body = post_event(actor_id="finance-agent", target_id="bank-api")
    event = body["event"]

    assert body["alerts"] == []
    assert event["reported_status"] == "allowed"
    assert event["platform_status"] == "allowed"
    assert uuid.UUID(event["event_id"])

    stored = client.get(f"/events/{event['event_id']}").json()
    assert stored["actor_id"] == "finance-agent"

    record = neo.run(
        "MATCH (a:Entity {id:'finance-agent'})-[r:API_CALL]->(t:Entity {id:'bank-api'}) "
        "RETURN r.count AS count"
    ).single()
    assert record["count"] == 1


def test_server_assigns_id_and_timestamp_when_omitted(post_event):
    event = post_event()["event"]
    assert event["event_id"]
    assert event["timestamp"].endswith(("Z", "+00:00")) or "T" in event["timestamp"]


def test_caller_supplied_id_is_honoured(post_event):
    given = str(uuid.uuid4())
    assert post_event(event_id=given)["event"]["event_id"] == given


def test_ingest_that_trips_a_rule_persists_an_alert(client, post_event, seed_baseline):
    seed_baseline()
    body = post_event(
        actor_id="payment-agent",
        target_id="external-agent-x",
        target_type="agent",
        action_type="delegation",
        permissions_used=["bank:transfer", "bank:admin"],
        metadata={"amount": 880_000.0},
        timestamp=(BASE_TIME + timedelta(hours=1)).isoformat(),
    )

    rules = {a["rule_name"] for a in body["alerts"]}
    assert rules == {"unseen_counterparty", "value_excursion", "new_permission"}
    assert body["event"]["platform_status"] == "suspicious"
    # every alert is durable, with a stable id and the evidence attached
    for alert in body["alerts"]:
        assert uuid.UUID(alert["alert_id"])
        assert alert["details"]["reason"]
        fetched = client.get(f"/alerts/{alert['alert_id']}")
        assert fetched.status_code == 200


def test_permissions_are_deduped_and_trimmed(post_event):
    event = post_event(permissions_used=["a:b", " a:b ", "", "c:d"])["event"]
    assert event["permissions_used"] == ["a:b", "c:d"]


def test_naive_timestamp_is_read_as_utc(post_event):
    event = post_event(timestamp="2026-01-01T10:00:00")["event"]
    assert event["timestamp"].startswith("2026-01-01T10:00:00")


def test_list_events_filters(client, post_event, seed_baseline):
    seed_baseline()
    post_event(
        actor_id="finance-agent",
        target_id="fx-rate-tool",
        target_type="tool",
        action_type="tool_call",
        permissions_used=["fx:read"],
    )

    assert len(client.get("/events?actor_id=payment-agent").json()) == 4
    assert len(client.get("/events?actor_id=finance-agent").json()) == 1
    assert len(client.get("/events?target_id=fx-rate-tool").json()) == 1
    assert len(client.get("/events?limit=2").json()) == 2


def test_list_events_is_newest_first(client, post_event):
    for i in range(3):
        post_event(
            metadata={"i": i}, timestamp=(BASE_TIME + timedelta(minutes=i)).isoformat()
        )
    stamps = [e["timestamp"] for e in client.get("/events").json()]
    assert stamps == sorted(stamps, reverse=True)


# --- the reported / platform status split -----------------------------------
def test_platform_verdict_diverges_without_touching_the_caller_claim(
    client, post_event, seed_baseline
):
    seed_baseline()
    body = post_event(
        actor_id="payment-agent",
        target_id="external-agent-x",
        target_type="agent",
        action_type="delegation",
        metadata={"amount": 880_000.0},
        timestamp=(BASE_TIME + timedelta(hours=1)).isoformat(),
    )
    assert body["event"]["reported_status"] == "allowed"
    assert body["event"]["platform_status"] == "suspicious"

    assert client.get("/events?reported_status=suspicious").json() == []
    assert len(client.get("/events?platform_status=suspicious").json()) == 1


def test_blocked_is_never_downgraded_by_a_rule(post_event, seed_baseline):
    """A rule firing must not soften a verdict the caller already made."""
    seed_baseline()
    body = post_event(
        actor_id="payment-agent",
        target_id="external-agent-x",
        target_type="agent",
        action_type="delegation",
        permissions_used=["bank:admin"],
        metadata={"amount": 880_000.0},
        reported_status="blocked",
        timestamp=(BASE_TIME + timedelta(hours=1)).isoformat(),
    )
    assert body["alerts"], "rules should still evaluate a blocked event"
    assert body["event"]["reported_status"] == "blocked"
    assert body["event"]["platform_status"] == "blocked"


def test_legacy_status_key_is_accepted_as_reported_status(client):
    payload = event_payload()
    payload["status"] = "blocked"
    body = client.post("/events", json=payload).json()
    assert body["event"]["reported_status"] == "blocked"


def test_cold_start_agent_is_not_judged(post_event):
    """Fewer than three clean events: nothing is flagged, however odd it looks."""
    body = post_event(
        actor_id="brand-new-agent",
        target_id="offshore-api",
        permissions_used=["net:egress"],
        metadata={"amount": 999_999.0},
    )
    assert body["alerts"] == []
    assert body["event"]["platform_status"] == "allowed"
