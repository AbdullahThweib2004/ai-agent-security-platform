"""The one window the staged write cannot close atomically.

Postgres is the durable log; Neo4j is a projection of it. They cannot share a
transaction, so ingest stages the writes: prepare the graph transaction, commit
Postgres, then commit the graph. If that last commit fails, the event is durable
but invisible in the graph.

Logging that is not evidence it heals. These tests assert the actual state
transition: row present / graph absent, then reconcile, then graph present with
no duplicate row.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.models.event import AgentEvent
from tests.conftest import event_payload

pytestmark = pytest.mark.integration


class _ExplodingTx:
    """A transaction whose writes work but whose commit always fails.

    Rolling back before raising is what a genuinely lost commit looks like: the
    staged writes never land.
    """

    def __init__(self, tx):
        self._tx = tx

    def run(self, *args, **kwargs):
        return self._tx.run(*args, **kwargs)

    def rollback(self):
        return self._tx.rollback()

    def commit(self):
        self._tx.rollback()
        raise RuntimeError("simulated neo4j commit failure")


class _ExplodingSession:
    def __init__(self, session):
        self._session = session

    def begin_transaction(self):
        return _ExplodingTx(self._session.begin_transaction())

    def run(self, *args, **kwargs):
        return self._session.run(*args, **kwargs)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return self._session.__exit__(*args)


class _ExplodingDriver:
    def __init__(self, driver):
        self._driver = driver

    def session(self, **kwargs):
        return _ExplodingSession(self._driver.session(**kwargs))


@pytest.fixture
def failing_graph_commit(monkeypatch):
    """Make only the graph *commit* fail, leaving Postgres fully functional."""
    from app.db.neo4j import get_driver as real_get_driver

    monkeypatch.setattr(
        "app.routers.events.get_driver",
        lambda: _ExplodingDriver(real_get_driver()),
    )


def graph_entity_count(neo, entity_id):
    return neo.run(
        "MATCH (n:Entity {id: $id}) RETURN count(n) AS c", id=entity_id
    ).single()["c"]


def graph_edge_count(neo, source, target):
    return neo.run(
        "MATCH (:Entity {id: $s})-[r]->(:Entity {id: $t}) RETURN count(r) AS c",
        s=source,
        t=target,
    ).single()["c"]


def test_failed_graph_commit_leaves_a_durable_row_and_an_empty_graph(
    client, session, neo, failing_graph_commit
):
    """Step 1-2: the event survives; the graph does not have it yet."""
    payload = event_payload(actor_id="finance-agent", target_id="bank-api")
    response = client.post("/events", json=payload)

    # The event is durable, so the request succeeds — discarding a security
    # event because a projection failed would be the worse outcome.
    assert response.status_code == 201, response.text
    event_id = response.json()["event"]["event_id"]

    stored = session.get(AgentEvent, event_id)
    assert stored is not None, "postgres row must survive the graph failure"
    assert stored.graph_projected is False, "row must be flagged for repair"

    assert graph_entity_count(neo, "finance-agent") == 0
    assert graph_entity_count(neo, "bank-api") == 0
    assert graph_edge_count(neo, "finance-agent", "bank-api") == 0


def test_reconcile_heals_the_graph_without_duplicating_the_row(
    client, session, neo, failing_graph_commit
):
    """Steps 1-4: the whole state transition, end to end."""
    payload = event_payload(actor_id="finance-agent", target_id="bank-api")
    event_id = client.post("/events", json=payload).json()["event"]["event_id"]

    # --- before ---
    assert graph_entity_count(neo, "finance-agent") == 0
    rows_before = session.scalar(select(func.count()).select_from(AgentEvent))
    assert rows_before == 1

    # --- replay (the graph driver is healthy again inside reconcile) ---
    result = client.post("/events/reconcile").json()
    assert result["pending"] == 1
    assert result["repaired"] == 1
    assert result["failed"] == 0
    assert result["repaired_event_ids"] == [event_id]

    # --- after ---
    assert graph_entity_count(neo, "finance-agent") == 1
    assert graph_entity_count(neo, "bank-api") == 1
    assert graph_edge_count(neo, "finance-agent", "bank-api") == 1

    session.expire_all()
    assert session.get(AgentEvent, event_id).graph_projected is True

    rows_after = session.scalar(select(func.count()).select_from(AgentEvent))
    assert rows_after == 1, "reconciliation must not create a second row"


def test_the_healed_event_appears_in_the_graph_api(client, neo, failing_graph_commit):
    """The repair is visible through the product, not just in the database."""
    client.post(
        "/events", json=event_payload(actor_id="finance-agent", target_id="bank-api")
    )
    assert client.get("/graph").json()["nodes"] == []

    client.post("/events/reconcile")

    graph = client.get("/graph").json()
    assert {n["id"] for n in graph["nodes"]} == {"finance-agent", "bank-api"}
    assert graph["edges"][0]["count"] == 1


def test_reconciling_twice_is_idempotent(client, neo, failing_graph_commit):
    """A second reconcile must not double-count the edge."""
    client.post(
        "/events", json=event_payload(actor_id="finance-agent", target_id="bank-api")
    )
    assert client.post("/events/reconcile").json()["repaired"] == 1

    second = client.post("/events/reconcile").json()
    assert second["pending"] == 0
    assert second["repaired"] == 0

    edge = neo.run(
        "MATCH (:Entity {id:'finance-agent'})-[r]->(:Entity {id:'bank-api'}) RETURN r.count AS c"
    ).single()
    assert (
        edge["c"] == 1
    ), "replaying an already-projected event must not inflate counts"


def test_re_ingesting_the_same_event_id_is_still_a_conflict(
    client, failing_graph_commit
):
    """Replay is reconciliation, not re-POST.

    The duplicate guard still protects the log, so the repair path must be the
    reconciler — which is precisely why it exists.
    """
    payload = event_payload(actor_id="finance-agent", target_id="bank-api")
    event_id = client.post("/events", json=payload).json()["event"]["event_id"]

    payload["event_id"] = event_id
    duplicate = client.post("/events", json=payload)
    assert duplicate.status_code == 409
    assert "already ingested" in duplicate.json()["detail"]


def test_a_backlog_of_several_events_is_healed_in_one_pass(
    client, neo, failing_graph_commit
):
    for i in range(5):
        client.post(
            "/events", json=event_payload(actor_id=f"agent-{i}", target_id="bank-api")
        )
    assert client.get("/graph").json()["nodes"] == []

    result = client.post("/events/reconcile").json()
    assert (result["pending"], result["repaired"], result["failed"]) == (5, 5, 0)

    graph = client.get("/graph").json()
    assert len(graph["nodes"]) == 6  # five agents plus bank-api
    assert graph["stats"]["edge_count"] == 5


def test_healthy_ingest_marks_the_event_projected(client, session):
    """The flag must be trustworthy in the normal case, or reconcile is noise."""
    event_id = client.post("/events", json=event_payload()).json()["event"]["event_id"]
    assert session.get(AgentEvent, event_id).graph_projected is True
    assert client.post("/events/reconcile").json()["pending"] == 0


def test_one_poisoned_event_does_not_block_the_rest_of_the_backlog(
    client, session, neo, monkeypatch, failing_graph_commit
):
    """Each event is reconciled independently, so a bad one is quarantined.

    Without per-event commits, a single unprojectable event would stall every
    later repair behind it — the graph would stay stale indefinitely.
    """
    for i in range(3):
        client.post(
            "/events", json=event_payload(actor_id=f"agent-{i}", target_id="bank-api")
        )

    import app.services.reconcile as reconcile_module

    real_project = reconcile_module.project_event
    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if kwargs.get("actor_id") == "agent-1":
            raise RuntimeError("this one cannot be projected")
        return real_project(*args, **kwargs)

    monkeypatch.setattr(reconcile_module, "project_event", flaky)

    result = client.post("/events/reconcile").json()
    assert result["pending"] == 3
    assert result["repaired"] == 2
    assert result["failed"] == 1
    assert result["failures"][0]["error"]

    # the two healthy events landed despite the failure between them
    assert graph_entity_count(neo, "agent-0") == 1
    assert graph_entity_count(neo, "agent-2") == 1
    assert graph_entity_count(neo, "agent-1") == 0

    # the failed one stays flagged, so a later pass will retry it
    monkeypatch.setattr(reconcile_module, "project_event", real_project)
    retry = client.post("/events/reconcile").json()
    assert (retry["pending"], retry["repaired"], retry["failed"]) == (1, 1, 0)
    assert graph_entity_count(neo, "agent-1") == 1
