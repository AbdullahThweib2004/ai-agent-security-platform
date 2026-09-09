"""Reconcile logging: backlog size, repairs, and loud quarantines."""

from __future__ import annotations

import json
import logging

import pytest

from app.logging_config import JsonFormatter
from tests.conftest import event_payload
from tests.integration.test_staged_write_failure import (  # noqa: F401
    failing_graph_commit,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def json_logs(caplog):
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


def test_staged_write_failure_is_logged_as_structured_error(
    client, json_logs, failing_graph_commit  # noqa: F811
):
    client.post("/events", json=event_payload(actor_id="finance-agent"))
    failures = by_event(json_logs(), "event.ingest.graph_commit_failed")
    assert len(failures) == 1
    entry = failures[0]
    assert entry["level"] == "ERROR"
    # Which store succeeded, which failed, and what to do about it.
    assert entry["postgres_write"] == "committed"
    assert entry["neo4j_write"] == "failed"
    assert entry["graph_projected"] is False
    assert entry["remediation"] == "POST /events/reconcile"
    assert entry["event_id"] and entry["actor_id"]
    assert entry["error_type"] == "RuntimeError"
    assert "exception" in entry


def test_reconcile_logs_backlog_and_outcome(
    client, json_logs, failing_graph_commit  # noqa: F811
):
    for i in range(3):
        client.post("/events", json=event_payload(actor_id=f"agent-{i}"))
    client.post("/events/reconcile")

    started = by_event(json_logs(), "reconcile.started")[-1]
    assert started["backlog_size"] == 3
    assert started["level"] == "INFO"

    completed = by_event(json_logs(), "reconcile.completed")[-1]
    assert completed["level"] == "INFO"
    assert completed["backlog_size"] == 3
    assert completed["repaired"] == 3
    assert completed["quarantined"] == 0

    repaired = by_event(json_logs(), "reconcile.event.repaired")
    assert len(repaired) == 3


def test_quarantined_events_are_loud_and_flagged(
    client, json_logs, monkeypatch, failing_graph_commit  # noqa: F811
):
    """A graph quietly missing events is a security tool lying by omission."""
    client.post("/events", json=event_payload(actor_id="agent-x"))

    import app.services.reconcile as reconcile_module

    def refuse(*args, **kwargs):
        raise RuntimeError("cannot project")

    monkeypatch.setattr(reconcile_module, "project_event", refuse)
    client.post("/events/reconcile")

    quarantined = by_event(json_logs(), "reconcile.event.quarantined")
    assert len(quarantined) == 1
    entry = quarantined[0]
    assert entry["level"] == "ERROR"
    assert entry["quarantined"] is True
    assert entry["alert_worthy"] is True
    assert entry["postgres_write"] == "committed"
    assert entry["neo4j_write"] == "failed"
    assert entry["actor_id"] == "agent-x"
    assert "QUARANTINE" in entry["message"]

    completed = by_event(json_logs(), "reconcile.completed")[-1]
    assert completed["level"] == "ERROR"
    assert completed["quarantined"] == 1
    assert completed["alert_worthy"] is True
    assert len(completed["quarantined_event_ids"]) == 1


def test_a_clean_reconcile_is_not_noisy(client, json_logs):
    client.post("/events", json=event_payload())
    client.post("/events/reconcile")
    completed = by_event(json_logs(), "reconcile.completed")[-1]
    assert completed["level"] == "INFO"
    assert completed["backlog_size"] == 0
    assert by_event(json_logs(), "reconcile.event.quarantined") == []
