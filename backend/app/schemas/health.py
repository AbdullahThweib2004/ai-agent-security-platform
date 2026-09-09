"""Health response contract."""

from __future__ import annotations

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Per-dependency health, not just an aggregate boolean.

    Each store reports ``"ok"`` or ``"error: <summary>"`` so a caller can tell
    which dependency failed without reading logs.
    """

    status: str = Field(description="healthy | degraded")
    postgres: str = Field(description='"ok", or "error: <summary>"')
    neo4j: str = Field(description='"ok", or "error: <summary>"')
    latency_ms: dict[str, float] = Field(
        default_factory=dict, description="Round-trip time of each probe"
    )
    app: str | None = None

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "status": "healthy",
                    "postgres": "ok",
                    "neo4j": "ok",
                    "latency_ms": {"postgres": 1.4, "neo4j": 2.9},
                },
                {
                    "status": "degraded",
                    "postgres": "ok",
                    "neo4j": "error: ServiceUnavailable: Unable to retrieve routing information",
                    "latency_ms": {"postgres": 1.2, "neo4j": 2000.0},
                },
            ]
        }
    }
