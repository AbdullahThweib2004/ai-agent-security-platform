"""Neo4j projection: idempotency and aggregation.

The graph is a projection of the event log, not a second source of truth. Every
write is a MERGE, so replaying the same stream must converge on the same graph
rather than growing duplicates.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from app.db.neo4j import project_event
from tests.conftest import BASE_TIME

pytestmark = pytest.mark.integration


def projection(**overrides):
    payload = {
        "event_id": str(uuid.uuid4()),
        "timestamp": BASE_TIME,
        "actor_type": "agent",
        "actor_id": "finance-agent",
        "target_type": "agent",
        "target_id": "payment-agent",
        "action_type": "delegation",
        "permissions_used": ["payments:initiate"],
        "platform_status": "allowed",
    }
    payload.update(overrides)
    return payload


def counts(neo):
    return {
        "nodes": neo.run("MATCH (n:Entity) RETURN count(n) AS c").single()["c"],
        "edges": neo.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"],
    }


def test_three_identical_events_make_one_edge_with_count_three(neo):
    """The stated guarantee: repeated behaviour aggregates, it does not duplicate."""
    for i in range(3):
        project_event(**projection(timestamp=BASE_TIME + timedelta(minutes=i)))

    assert counts(neo) == {"nodes": 2, "edges": 1}
    edge = neo.run(
        "MATCH (:Entity {id:'finance-agent'})-[r:DELEGATION]->(:Entity {id:'payment-agent'}) "
        "RETURN r.count AS count, r.first_seen AS first, r.last_seen AS last"
    ).single()
    assert edge["count"] == 3
    assert edge["first"] < edge["last"]


def test_replaying_the_same_event_id_is_convergent(neo):
    """Re-projecting after a crash must not corrupt the graph's shape."""
    payload = projection()
    for _ in range(5):
        project_event(**payload)
    assert counts(neo) == {"nodes": 2, "edges": 1}


def test_entity_ids_stay_unique_across_labels(neo):
    """One id is one node, whatever role it plays."""
    project_event(**projection())
    project_event(
        **projection(
            actor_id="payment-agent",
            target_id="bank-api",
            target_type="api",
            action_type="api_call",
        )
    )
    assert counts(neo) == {"nodes": 3, "edges": 2}
    duplicates = neo.run(
        "MATCH (n:Entity) WITH n.id AS id, count(*) AS c WHERE c > 1 RETURN count(*) AS d"
    ).single()["d"]
    assert duplicates == 0


def test_different_action_types_are_separate_edges(neo):
    project_event(**projection())
    project_event(
        **projection(
            action_type="tool_call", target_type="tool", target_id="payment-agent"
        )
    )
    assert counts(neo)["edges"] == 2


def test_permissions_accumulate_as_a_set(neo):
    project_event(**projection(permissions_used=["a:1"]))
    project_event(**projection(permissions_used=["a:1", "b:2"]))
    project_event(**projection(permissions_used=["b:2"]))
    perms = neo.run("MATCH ()-[r:DELEGATION]->() RETURN r.permissions AS p").single()[
        "p"
    ]
    assert sorted(perms) == ["a:1", "b:2"]


def test_status_counters_accumulate_on_node_and_edge(neo):
    project_event(**projection())
    project_event(**projection(platform_status="suspicious"))
    project_event(**projection(platform_status="blocked"))

    edge = neo.run(
        "MATCH ()-[r:DELEGATION]->() RETURN r.count AS c, r.suspicious_count AS s, "
        "r.blocked_count AS b, r.last_platform_status AS last"
    ).single()
    assert (edge["c"], edge["s"], edge["b"]) == (3, 1, 1)
    assert edge["last"] == "blocked"

    actor = neo.run(
        "MATCH (n:Entity {id:'finance-agent'}) RETURN n.event_count AS e, n.suspicious_count AS s"
    ).single()
    assert (actor["e"], actor["s"]) == (3, 1)


def test_inbound_and_outbound_counters_are_separate(neo):
    """A passive target initiates nothing; reporting 0 activity would mislead."""
    for _ in range(4):
        project_event(**projection())
    node = neo.run(
        "MATCH (n:Entity {id:'payment-agent'}) "
        "RETURN n.event_count AS out, n.inbound_count AS inb"
    ).single()
    assert (node["out"], node["inb"]) == (0, 4)


def test_ingesting_the_same_stream_twice_yields_the_same_graph(client, post_event, neo):
    """End-to-end idempotency, through the API rather than the driver."""
    events = [
        dict(
            actor_id="finance-agent",
            target_id="payment-agent",
            target_type="agent",
            action_type="delegation",
        ),
        dict(actor_id="payment-agent", target_id="bank-api"),
        dict(actor_id="payment-agent", target_id="bank-api"),
    ]
    for e in events:
        post_event(**e)
    first = counts(neo)

    for e in events:
        post_event(**e)  # fresh event ids, same behaviour
    second = counts(neo)

    assert first == second, "repeating known behaviour must not add nodes or edges"
    edge = neo.run(
        "MATCH (:Entity {id:'payment-agent'})-[r:API_CALL]->(:Entity {id:'bank-api'}) "
        "RETURN r.count AS c"
    ).single()
    assert edge["c"] == 4


@pytest.mark.parametrize(
    "field, value",
    [
        ("actor_type", "robot"),
        ("target_type", "spaceship"),
        ("action_type", "telepathy"),
    ],
)
def test_unknown_types_are_rejected_before_touching_the_graph(neo, field, value):
    """Relationship types are interpolated into Cypher, so the whitelist matters."""
    with pytest.raises(ValueError):
        project_event(**projection(**{field: value}))
    assert counts(neo) == {"nodes": 0, "edges": 0}
