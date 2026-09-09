"""What the ingest path actually logs.

Two things are being pinned: that the queryable fields are present at the right
levels, and — just as important for a security tool — that event metadata and
alert evidence never reach the logs.
"""

from __future__ import annotations

import json
import logging

import pytest

from app.logging_config import JsonFormatter
from tests.conftest import BASE_TIME, event_payload

pytestmark = pytest.mark.integration

SECRET_METADATA = {
    "amount": 880000.0,
    "query": "SELECT * FROM customers",
    "vendor": "Globex Consulting",
    "account": "IBAN-SECRET-123",
}


@pytest.fixture
def json_logs(caplog):
    """Capture records and render them exactly as the app would emit them."""
    caplog.set_level(logging.DEBUG)

    def rendered() -> list[dict]:
        out = []
        for record in caplog.records:
            try:
                out.append(json.loads(JsonFormatter().format(record)))
            except Exception:
                continue
        return out

    return rendered


def by_event(logs, name):
    return [entry for entry in logs if entry.get("event") == name]


def test_ingest_logs_the_received_event_at_info(client, json_logs):
    client.post("/events", json=event_payload(actor_id="finance-agent"))
    received = by_event(json_logs(), "event.ingest.received")
    assert len(received) == 1
    entry = received[0]
    assert entry["level"] == "INFO"
    assert entry["actor_id"] == "finance-agent"
    assert entry["target_id"] == "bank-api"
    assert entry["action_type"] == "api_call"
    assert entry["event_id"]


def test_rule_evaluation_records_what_ran_fired_and_passed(
    client, json_logs, seed_baseline
):
    seed_baseline()
    from datetime import timedelta

    client.post(
        "/events",
        json=event_payload(
            actor_id="payment-agent",
            target_id="external-agent-x",
            target_type="agent",
            action_type="delegation",
            permissions_used=["bank:admin"],
            metadata={"amount": 880_000.0},
            timestamp=(BASE_TIME + timedelta(hours=1)).isoformat(),
        ),
    )
    evaluated = by_event(json_logs(), "event.rules.evaluated")[-1]
    assert evaluated["baseline_established"] is True
    assert set(evaluated["rules_evaluated"]) == {
        "unseen_counterparty",
        "value_excursion",
        "new_permission",
    }
    assert set(evaluated["rules_fired"]) == {
        "unseen_counterparty",
        "value_excursion",
        "new_permission",
    }
    assert evaluated["rules_passed"] == []


def test_a_rule_that_does_not_fire_is_recorded_as_passed(
    client, json_logs, seed_baseline
):
    seed_baseline()
    from datetime import timedelta

    client.post(
        "/events",
        json=event_payload(
            actor_id="payment-agent",
            metadata={"amount": 1050.0},
            timestamp=(BASE_TIME + timedelta(hours=1)).isoformat(),
        ),
    )
    evaluated = by_event(json_logs(), "event.rules.evaluated")[-1]
    assert evaluated["rules_fired"] == []
    assert set(evaluated["rules_passed"]) == {
        "unseen_counterparty",
        "value_excursion",
        "new_permission",
    }


def test_cold_start_says_why_nothing_was_judged(client, json_logs):
    """An empty rules_fired must not be mistakable for a clean bill of health."""
    client.post("/events", json=event_payload(actor_id="brand-new-agent"))
    evaluated = by_event(json_logs(), "event.rules.evaluated")[-1]
    assert evaluated["baseline_established"] is False
    assert evaluated["rules_evaluated"] == []
    assert evaluated["skipped_reason"] == "baseline_not_established"


def test_alerts_are_logged_at_warning_with_rule_and_severity(
    client, json_logs, seed_baseline
):
    seed_baseline()
    from datetime import timedelta

    client.post(
        "/events",
        json=event_payload(
            actor_id="payment-agent",
            target_id="external-agent-x",
            target_type="agent",
            action_type="delegation",
            permissions_used=["bank:admin"],
            metadata=SECRET_METADATA,
            timestamp=(BASE_TIME + timedelta(hours=1)).isoformat(),
        ),
    )
    raised = by_event(json_logs(), "alert.raised")
    assert len(raised) == 3
    assert {entry["level"] for entry in raised} == {"WARNING"}
    assert {entry["rule"] for entry in raised} == {
        "unseen_counterparty",
        "value_excursion",
        "new_permission",
    }
    assert all(entry["severity"] in {"high", "medium", "low"} for entry in raised)
    assert all(entry["alert_id"] and entry["event_id"] for entry in raised)


def test_completion_records_both_write_results_and_the_status_outcome(
    client, json_logs, seed_baseline
):
    seed_baseline()
    from datetime import timedelta

    client.post(
        "/events",
        json=event_payload(
            actor_id="payment-agent",
            target_id="external-agent-x",
            target_type="agent",
            action_type="delegation",
            metadata={"amount": 880_000.0},
            timestamp=(BASE_TIME + timedelta(hours=1)).isoformat(),
        ),
    )
    completed = by_event(json_logs(), "event.ingest.completed")[-1]
    assert completed["level"] == "INFO"
    assert completed["postgres_write"] == "committed"
    assert completed["neo4j_write"] == "committed"
    assert completed["graph_projected"] is True
    assert completed["reported_status"] == "allowed"
    assert completed["platform_status"] == "suspicious"
    assert completed["status_overridden"] is True
    assert completed["alert_count"] >= 1


def test_rejections_are_logged_with_a_machine_readable_reason(client, json_logs):
    client.post(
        "/events",
        json=event_payload(parent_event_id="99999999-9999-9999-9999-999999999999"),
    )
    rejected = by_event(json_logs(), "event.ingest.rejected")[-1]
    assert rejected["level"] == "WARNING"
    assert rejected["reason"] == "unknown_parent"


# --- the part that matters most for a security tool -------------------------
def test_event_metadata_never_reaches_the_logs(client, json_logs, seed_baseline):
    """Amounts, query text and counterparties stay in Postgres, not in logs."""
    seed_baseline()
    from datetime import timedelta

    client.post(
        "/events",
        json=event_payload(
            actor_id="payment-agent",
            target_id="external-agent-x",
            target_type="agent",
            action_type="delegation",
            permissions_used=["bank:admin"],
            metadata=SECRET_METADATA,
            timestamp=(BASE_TIME + timedelta(hours=1)).isoformat(),
        ),
    )
    blob = json.dumps(json_logs())
    for secret in ("SELECT * FROM customers", "IBAN-SECRET-123", "Globex Consulting"):
        assert secret not in blob, f"{secret!r} leaked into the logs"


def test_alert_evidence_is_not_duplicated_into_the_logs(
    client, json_logs, seed_baseline
):
    seed_baseline()
    from datetime import timedelta

    client.post(
        "/events",
        json=event_payload(
            actor_id="payment-agent",
            target_id="external-agent-x",
            target_type="agent",
            action_type="delegation",
            metadata={"amount": 880_000.0},
            timestamp=(BASE_TIME + timedelta(hours=1)).isoformat(),
        ),
    )
    for entry in by_event(json_logs(), "alert.raised"):
        assert "details" not in entry
        assert "reason" not in entry
        assert "baseline_max" not in entry


def test_permission_names_are_counted_not_copied_on_receipt(client, json_logs):
    client.post(
        "/events", json=event_payload(permissions_used=["bank:transfer", "bank:admin"])
    )
    received = by_event(json_logs(), "event.ingest.received")[-1]
    assert received["permission_count"] == 2
    assert "permissions_used" not in received
