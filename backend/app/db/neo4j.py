"""Neo4j driver lifecycle, schema constraints, and MERGE-based graph projection.

The graph is a *projection* of the Postgres event log, not a second source of
truth. Nodes are entities (agents, users, tools, APIs, databases) and edges are
aggregated relationships between them: one edge per (actor, action, target)
triple, carrying counts and first/last-seen rather than one edge per event.
That keeps the behavior graph readable as volume grows, and makes replaying the
same event stream idempotent.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from neo4j import Driver, GraphDatabase

from app.config import get_settings

logger = logging.getLogger(__name__)

_driver: Driver | None = None

# Relationship types cannot be parameterised in Cypher, so they are interpolated
# into the query string. Only values in this whitelist are ever interpolated.
_REL_TYPES = {
    "tool_call": "TOOL_CALL",
    "delegation": "DELEGATION",
    "data_access": "DATA_ACCESS",
    "api_call": "API_CALL",
}

# Entity type -> secondary node label.
_NODE_LABELS = {
    "user": "User",
    "agent": "Agent",
    "tool": "Tool",
    "api": "Api",
    "database": "Database",
}


def get_driver() -> Driver:
    """Return the process-wide driver, creating it on first use."""
    global _driver
    if _driver is None:
        settings = get_settings()
        _driver = GraphDatabase.driver(
            settings.neo4j_uri,
            auth=(settings.neo4j_user, settings.neo4j_password),
            max_connection_lifetime=3600,
        )
    return _driver


def close_driver() -> None:
    """Close the driver on application shutdown."""
    global _driver
    if _driver is not None:
        _driver.close()
        _driver = None


def verify_connectivity() -> None:
    get_driver().verify_connectivity()


def init_constraints() -> None:
    """Uniqueness constraints on entity IDs.

    Every node carries the shared ``:Entity`` label plus a type-specific label,
    so a single constraint keeps IDs unique across the whole graph and gives
    MERGE an index to seek on.
    """
    statements = [
        "CREATE CONSTRAINT entity_id_unique IF NOT EXISTS "
        "FOR (e:Entity) REQUIRE e.id IS UNIQUE",
        "CREATE INDEX entity_type_idx IF NOT EXISTS FOR (e:Entity) ON (e.type)",
    ]
    with get_driver().session() as session:
        for stmt in statements:
            session.run(stmt)
    logger.info("neo4j constraints ensured")


def _label_for(entity_type: str) -> str:
    label = _NODE_LABELS.get(entity_type)
    if label is None:
        raise ValueError(f"unknown entity type: {entity_type!r}")
    return label


def _rel_for(action_type: str) -> str:
    rel = _REL_TYPES.get(action_type)
    if rel is None:
        raise ValueError(f"unknown action type: {action_type!r}")
    return rel


def _iso(ts: datetime) -> str:
    return ts.isoformat()


def project_event(
    *,
    event_id: str,
    timestamp: datetime,
    actor_type: str,
    actor_id: str,
    target_type: str,
    target_id: str,
    action_type: str,
    permissions_used: list[str],
    platform_status: str,
    tx: Any | None = None,
) -> None:
    """Upsert the two entities and the edge implied by one event.

    ``platform_status`` is deliberately the platform's verdict, not the
    caller's claim: the graph highlights what we concluded, not what an agent
    asserted about itself.

    Idempotent: re-projecting the same event stream converges on the same graph
    rather than duplicating nodes or edges.
    """
    actor_label = _label_for(actor_type)
    target_label = _label_for(target_type)
    rel_type = _rel_for(action_type)

    query = f"""
    MERGE (a:Entity {{id: $actor_id}})
      ON CREATE SET a.type = $actor_type, a.first_seen = $ts, a.event_count = 0,
                    a.inbound_count = 0, a.suspicious_count = 0, a.blocked_count = 0
    SET a:{actor_label},
        a.type = $actor_type,
        a.last_seen = $ts,
        a.event_count = coalesce(a.event_count, 0) + 1,
        a.suspicious_count = coalesce(a.suspicious_count, 0)
            + CASE WHEN $status = 'suspicious' THEN 1 ELSE 0 END,
        a.blocked_count = coalesce(a.blocked_count, 0)
            + CASE WHEN $status = 'blocked' THEN 1 ELSE 0 END

    MERGE (t:Entity {{id: $target_id}})
      ON CREATE SET t.type = $target_type, t.first_seen = $ts, t.event_count = 0,
                    t.inbound_count = 0, t.suspicious_count = 0, t.blocked_count = 0
    SET t:{target_label},
        t.type = $target_type,
        t.last_seen = $ts,
        t.inbound_count = coalesce(t.inbound_count, 0) + 1

    MERGE (a)-[r:{rel_type}]->(t)
      ON CREATE SET r.first_seen = $ts, r.count = 0, r.suspicious_count = 0,
                    r.blocked_count = 0, r.permissions = []
    SET r.action_type = $action_type,
        r.last_seen = $ts,
        r.count = coalesce(r.count, 0) + 1,
        r.last_event_id = $event_id,
        r.last_platform_status = $status,
        r.suspicious_count = coalesce(r.suspicious_count, 0)
            + CASE WHEN $status = 'suspicious' THEN 1 ELSE 0 END,
        r.blocked_count = coalesce(r.blocked_count, 0)
            + CASE WHEN $status = 'blocked' THEN 1 ELSE 0 END
    WITH r
    SET r.permissions = coalesce(r.permissions, [])
        + [p IN $permissions WHERE NOT p IN coalesce(r.permissions, [])]
    """

    params = {
        "event_id": event_id,
        "ts": _iso(timestamp),
        "actor_id": actor_id,
        "actor_type": actor_type,
        "target_id": target_id,
        "target_type": target_type,
        "action_type": action_type,
        "permissions": permissions_used or [],
        "status": platform_status,
    }

    if tx is not None:
        tx.run(query, **params)
    else:
        with get_driver().session() as session:
            session.run(query, **params)


def fetch_full_graph(limit: int = 500) -> dict[str, list[dict]]:
    """Every entity and relationship in the graph."""
    query = """
    MATCH (a:Entity)-[r]->(t:Entity)
    RETURN a, r, t, type(r) AS rel_type
    ORDER BY r.last_seen DESC
    LIMIT $limit
    """
    with get_driver().session() as session:
        records = list(session.run(query, limit=limit))
        # Entities with no edges yet would otherwise be invisible.
        isolated = list(
            session.run(
                """
                MATCH (e:Entity)
                WHERE NOT (e)--()
                RETURN e
                LIMIT $limit
                """,
                limit=limit,
            )
        )
    return _assemble(records, isolated)


def fetch_agent_graph(agent_id: str, depth: int = 1) -> dict[str, list[dict]]:
    """The neighbourhood around one entity, out to ``depth`` hops."""
    depth = max(1, min(int(depth), 4))
    query = f"""
    MATCH (center:Entity {{id: $agent_id}})
    OPTIONAL MATCH path = (center)-[*1..{depth}]-(:Entity)
    WITH center, relationships(path) AS rels
    UNWIND CASE WHEN rels IS NULL THEN [null] ELSE rels END AS r
    WITH center, r
    WHERE r IS NOT NULL
    RETURN startNode(r) AS a, r, endNode(r) AS t, type(r) AS rel_type
    """
    with get_driver().session() as session:
        records = list(session.run(query, agent_id=agent_id, depth=depth))
        center = list(
            session.run("MATCH (e:Entity {id: $agent_id}) RETURN e", agent_id=agent_id)
        )
    if not center:
        return {"nodes": [], "edges": []}
    return _assemble(records, center)


def _node_dict(node) -> dict:
    return {
        "id": node.get("id"),
        "type": node.get("type"),
        "labels": sorted(label for label in node.labels if label != "Entity"),
        "first_seen": node.get("first_seen"),
        "last_seen": node.get("last_seen"),
        # Actions this entity initiated vs. actions performed against it. A
        # passive target like an API initiates nothing, so reporting only the
        # former would show it as inactive no matter how heavily it is called.
        "event_count": node.get("event_count") or 0,
        "inbound_count": node.get("inbound_count") or 0,
        "suspicious_count": node.get("suspicious_count") or 0,
        "blocked_count": node.get("blocked_count") or 0,
    }


def _assemble(records, extra_nodes) -> dict[str, list[dict]]:
    nodes: dict[str, dict] = {}
    edges: dict[tuple, dict] = {}

    for rec in records:
        for key in ("a", "t"):
            n = rec[key]
            nodes[n.get("id")] = _node_dict(n)
        r = rec["r"]
        source = rec["a"].get("id")
        target = rec["t"].get("id")
        rel_type = rec["rel_type"]
        edges[(source, target, rel_type)] = {
            "source": source,
            "target": target,
            "rel_type": rel_type,
            "action_type": r.get("action_type"),
            "count": r.get("count") or 0,
            "permissions": r.get("permissions") or [],
            "first_seen": r.get("first_seen"),
            "last_seen": r.get("last_seen"),
            "last_platform_status": r.get("last_platform_status"),
            "last_event_id": r.get("last_event_id"),
            "suspicious_count": r.get("suspicious_count") or 0,
            "blocked_count": r.get("blocked_count") or 0,
        }

    for rec in extra_nodes:
        n = rec["e"]
        nodes.setdefault(n.get("id"), _node_dict(n))

    return {"nodes": list(nodes.values()), "edges": list(edges.values())}
