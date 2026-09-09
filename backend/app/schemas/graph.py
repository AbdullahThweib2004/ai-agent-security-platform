"""Response schemas for the behavior graph."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class GraphNode(BaseModel):
    id: str
    type: str
    labels: list[str] = Field(default_factory=list)
    first_seen: str | None = None
    last_seen: str | None = None
    event_count: int = Field(default=0, description="Actions this entity initiated")
    inbound_count: int = Field(default=0, description="Actions taken against it")
    suspicious_count: int = 0
    blocked_count: int = 0
    alert_count: int = 0
    clean_event_count: int = Field(
        default=0, description="Events that count toward this entity's baseline"
    )
    health: str = Field(
        default="healthy",
        description="healthy | suspicious | unrated (too little history to judge)",
    )


class GraphEdge(BaseModel):
    source: str
    target: str
    rel_type: str
    action_type: str | None = None
    count: int = 0
    permissions: list[str] = Field(default_factory=list)
    first_seen: str | None = None
    last_seen: str | None = None
    last_platform_status: str | None = None
    last_event_id: str | None = None
    suspicious_count: int = 0
    blocked_count: int = 0
    suspicious: bool = False


class GraphStats(BaseModel):
    node_count: int = 0
    edge_count: int = 0
    agent_count: int = 0
    suspicious_node_count: int = 0
    unrated_node_count: int = 0
    suspicious_edge_count: int = 0


class GraphResponse(BaseModel):
    nodes: list[GraphNode] = Field(default_factory=list)
    edges: list[GraphEdge] = Field(default_factory=list)
    stats: GraphStats = Field(default_factory=GraphStats)


class BaselineOut(BaseModel):
    """The 'usual behaviour' summary the user asked to surface per agent."""

    agent_id: str
    event_count: int
    is_established: bool
    usual_action_types: list[str] = Field(default_factory=list)
    usual_tools: list[str] = Field(default_factory=list)
    usual_agents_contacted: list[str] = Field(default_factory=list)
    usual_apis: list[str] = Field(default_factory=list)
    usual_databases: list[str] = Field(default_factory=list)
    usual_permissions: list[str] = Field(default_factory=list)
    typical_value_range: dict[str, Any] = Field(default_factory=dict)
    first_seen: str | None = None
    last_seen: str | None = None


class AgentGraphResponse(GraphResponse):
    agent_id: str
    baseline: BaselineOut
