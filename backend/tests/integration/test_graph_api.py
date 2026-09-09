"""GET /graph and GET /graph/{agent_id}."""

from __future__ import annotations

from datetime import timedelta

import pytest

from tests.conftest import BASE_TIME

pytestmark = pytest.mark.integration


def find_node(graph, node_id):
    return next((n for n in graph["nodes"] if n["id"] == node_id), None)


def find_edge(graph, source, target):
    return next(
        (e for e in graph["edges"] if e["source"] == source and e["target"] == target),
        None,
    )


def test_empty_graph(client):
    graph = client.get("/graph").json()
    assert graph["nodes"] == []
    assert graph["edges"] == []
    assert graph["stats"]["node_count"] == 0


def test_graph_reports_entities_edges_and_counts(client, seed_baseline):
    seed_baseline()
    graph = client.get("/graph").json()

    assert {n["id"] for n in graph["nodes"]} == {"payment-agent", "bank-api"}
    agent = find_node(graph, "payment-agent")
    api = find_node(graph, "bank-api")
    assert agent["type"] == "agent"
    assert agent["event_count"] == 4  # actions initiated
    assert agent["inbound_count"] == 0
    assert api["event_count"] == 0  # a passive target initiates nothing
    assert api["inbound_count"] == 4

    edge = find_edge(graph, "payment-agent", "bank-api")
    assert edge["count"] == 4
    assert edge["rel_type"] == "API_CALL"
    assert edge["permissions"] == ["bank:transfer"]
    assert edge["suspicious"] is False


def test_suspicion_is_flagged_on_nodes_and_edges(client, post_event, seed_baseline):
    seed_baseline()
    post_event(
        actor_id="payment-agent",
        target_id="external-agent-x",
        target_type="agent",
        action_type="delegation",
        permissions_used=["bank:admin"],
        metadata={"amount": 880_000.0},
        timestamp=(BASE_TIME + timedelta(hours=1)).isoformat(),
    )
    graph = client.get("/graph").json()

    assert find_node(graph, "payment-agent")["health"] == "suspicious"
    assert find_node(graph, "payment-agent")["alert_count"] == 3
    assert find_edge(graph, "payment-agent", "external-agent-x")["suspicious"] is True
    assert find_edge(graph, "payment-agent", "bank-api")["suspicious"] is False
    assert graph["stats"]["suspicious_node_count"] == 1
    assert graph["stats"]["suspicious_edge_count"] == 1


def test_cold_start_agent_reads_unrated_not_healthy(client, post_event):
    post_event(actor_id="brand-new-agent", target_id="offshore-api")
    graph = client.get("/graph").json()
    assert find_node(graph, "brand-new-agent")["health"] == "unrated"
    assert graph["stats"]["unrated_node_count"] == 1


def test_agent_scoped_graph_returns_the_neighbourhood_and_baseline(
    client, seed_baseline
):
    seed_baseline()
    scoped = client.get("/graph/payment-agent").json()

    assert scoped["agent_id"] == "payment-agent"
    assert {n["id"] for n in scoped["nodes"]} == {"payment-agent", "bank-api"}
    baseline = scoped["baseline"]
    assert baseline["is_established"] is True
    assert baseline["event_count"] == 4
    assert baseline["usual_apis"] == ["bank-api"]
    assert baseline["usual_agents_contacted"] == []
    assert baseline["usual_permissions"] == ["bank:transfer"]
    assert baseline["typical_value_range"]["min"] == 900.0
    assert baseline["typical_value_range"]["max"] == 1200.0
    assert baseline["typical_value_range"]["samples"] == 4


def test_scoped_baseline_excludes_what_it_flagged(client, post_event, seed_baseline):
    """The displayed 'normal' must not absorb the very event we called abnormal."""
    seed_baseline()
    post_event(
        actor_id="payment-agent",
        target_id="external-agent-x",
        target_type="agent",
        action_type="delegation",
        permissions_used=["bank:admin"],
        metadata={"amount": 880_000.0},
        timestamp=(BASE_TIME + timedelta(hours=1)).isoformat(),
    )
    baseline = client.get("/graph/payment-agent").json()["baseline"]
    assert baseline["typical_value_range"]["max"] == 1200.0
    assert baseline["usual_agents_contacted"] == []
    assert "bank:admin" not in baseline["usual_permissions"]


def test_depth_widens_the_neighbourhood(client, post_event, seed_baseline):
    seed_baseline()
    post_event(
        actor_id="analyst-1",
        actor_type="user",
        target_id="payment-agent",
        target_type="agent",
        action_type="delegation",
    )
    post_event(
        actor_id="bank-api",
        actor_type="agent",
        target_id="ledger-tool",
        target_type="tool",
        action_type="tool_call",
    )

    one_hop = client.get("/graph/analyst-1?depth=1").json()
    two_hop = client.get("/graph/analyst-1?depth=2").json()
    assert {n["id"] for n in one_hop["nodes"]} == {"analyst-1", "payment-agent"}
    assert "bank-api" in {n["id"] for n in two_hop["nodes"]}


def test_unknown_agent_is_404_not_an_empty_graph(client):
    response = client.get("/graph/does-not-exist")
    assert response.status_code == 404
    assert "does-not-exist" in response.json()["detail"]


@pytest.mark.parametrize("depth", [0, 5, -1])
def test_depth_outside_the_allowed_range_is_rejected(client, seed_baseline, depth):
    seed_baseline()
    assert client.get(f"/graph/payment-agent?depth={depth}").status_code == 422
