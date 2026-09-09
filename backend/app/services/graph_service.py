"""Behavior graph assembly.

Reads the entity graph out of Neo4j and enriches it with facts only Postgres
knows: how many alerts each entity has actually raised, and what an agent's
baseline looks like. Neo4j answers "who talks to whom"; Postgres answers
"and was any of it a problem".
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import neo4j as graph_db
from app.models.event import AgentEvent, Alert
from app.schemas.graph import (
    AgentGraphResponse,
    BaselineOut,
    GraphEdge,
    GraphNode,
    GraphResponse,
    GraphStats,
)
from app.services.baseline import (
    _EXCLUDED_FROM_BASELINE,
    MIN_BASELINE_EVENTS,
    AgentBaseline,
    compute_baseline,
)


def _alert_counts_by_actor(session: Session) -> dict[str, int]:
    """How many alerts each actor is responsible for."""
    stmt = (
        select(AgentEvent.actor_id, func.count(Alert.alert_id))
        .join(Alert, Alert.event_id == AgentEvent.event_id)
        .group_by(AgentEvent.actor_id)
    )
    return {actor_id: count for actor_id, count in session.execute(stmt)}


def _clean_event_counts(session: Session) -> dict[str, int]:
    """Events per actor that actually count toward a baseline."""
    stmt = (
        select(AgentEvent.actor_id, func.count(AgentEvent.event_id))
        .where(AgentEvent.platform_status.notin_(tuple(_EXCLUDED_FROM_BASELINE)))
        .group_by(AgentEvent.actor_id)
    )
    return {actor_id: count for actor_id, count in session.execute(stmt)}


def _health_of(node: dict, alert_count: int, clean_count: int) -> str:
    """Three states, not two.

    Reporting an agent as "healthy" when the rules have not actually been able
    to judge it is a false reassurance — it is exactly the freshly-introduced
    agent that most warrants a second look. Below the baseline threshold the
    honest answer is "unrated", not "healthy".
    """
    if alert_count > 0 or node["suspicious_count"] > 0:
        return "suspicious"
    if node["type"] == "agent" and clean_count < MIN_BASELINE_EVENTS:
        return "unrated"
    return "healthy"


def _build(
    raw: dict, alert_counts: dict[str, int], clean_counts: dict[str, int]
) -> tuple[list[GraphNode], list[GraphEdge]]:
    nodes = []
    for n in raw["nodes"]:
        alert_count = alert_counts.get(n["id"], 0)
        clean_count = clean_counts.get(n["id"], 0)
        nodes.append(
            GraphNode(
                **n,
                alert_count=alert_count,
                clean_event_count=clean_count,
                health=_health_of(n, alert_count, clean_count),
            )
        )
    edges = [
        GraphEdge(
            **e,
            suspicious=(
                e["suspicious_count"] > 0 or e["last_platform_status"] == "suspicious"
            ),
        )
        for e in raw["edges"]
    ]
    return nodes, edges


def _stats(nodes: list[GraphNode], edges: list[GraphEdge]) -> GraphStats:
    return GraphStats(
        node_count=len(nodes),
        edge_count=len(edges),
        agent_count=sum(1 for n in nodes if n.type == "agent"),
        suspicious_node_count=sum(1 for n in nodes if n.health == "suspicious"),
        unrated_node_count=sum(1 for n in nodes if n.health == "unrated"),
        suspicious_edge_count=sum(1 for e in edges if e.suspicious),
    )


def get_full_graph(session: Session, limit: int = 500) -> GraphResponse:
    raw = graph_db.fetch_full_graph(limit=limit)
    nodes, edges = _build(
        raw, _alert_counts_by_actor(session), _clean_event_counts(session)
    )
    return GraphResponse(nodes=nodes, edges=edges, stats=_stats(nodes, edges))


def _baseline_out(baseline: AgentBaseline, session: Session) -> BaselineOut:
    """Split the baseline's known targets by entity type for readability.

    The baseline itself keeps a flat set of target IDs; the type of each is
    recovered from the log so the UI can say "usual tools" and "usual agents
    contacted" separately, as requested.
    """
    types: dict[str, str] = {}
    if baseline.known_targets:
        stmt = (
            select(AgentEvent.target_id, AgentEvent.target_type)
            .where(AgentEvent.target_id.in_(baseline.known_targets))
            .distinct()
        )
        types = {tid: ttype for tid, ttype in session.execute(stmt)}

    def of_type(t: str) -> list[str]:
        return sorted(tid for tid in baseline.known_targets if types.get(tid) == t)

    return BaselineOut(
        agent_id=baseline.agent_id,
        event_count=baseline.event_count,
        is_established=baseline.is_established,
        usual_action_types=sorted(baseline.known_action_types),
        usual_tools=of_type("tool"),
        usual_agents_contacted=of_type("agent"),
        usual_apis=of_type("api"),
        usual_databases=of_type("database"),
        usual_permissions=sorted(baseline.known_permissions),
        typical_value_range={
            "min": baseline.value_min,
            "max": baseline.value_max,
            "mean": (
                round(baseline.value_mean, 2)
                if baseline.value_mean is not None
                else None
            ),
            "stdev": (
                round(baseline.value_stdev, 2)
                if baseline.value_stdev is not None
                else None
            ),
            "samples": len(baseline.values),
        },
        first_seen=baseline.first_seen.isoformat() if baseline.first_seen else None,
        last_seen=baseline.last_seen.isoformat() if baseline.last_seen else None,
    )


def get_agent_graph(
    session: Session, agent_id: str, depth: int = 1
) -> AgentGraphResponse | None:
    raw = graph_db.fetch_agent_graph(agent_id, depth=depth)
    if not raw["nodes"]:
        return None
    nodes, edges = _build(
        raw, _alert_counts_by_actor(session), _clean_event_counts(session)
    )
    baseline = compute_baseline(session, agent_id)
    return AgentGraphResponse(
        agent_id=agent_id,
        nodes=nodes,
        edges=edges,
        stats=_stats(nodes, edges),
        baseline=_baseline_out(baseline, session),
    )
