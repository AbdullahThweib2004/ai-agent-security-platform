"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from neo4j.exceptions import Neo4jError, ServiceUnavailable
from sqlalchemy.exc import OperationalError, SQLAlchemyError

from app.config import get_settings
from app.db.neo4j import close_driver, init_constraints, verify_connectivity
from app.db.postgres import init_db
from app.logging_config import configure_logging
from app.routers import (
    a2a,
    agents,
    alerts,
    delegations,
    events,
    forensics,
    graph,
    incidents,
)
from app.schemas.health import HealthResponse
from app.services.health import health_report

settings = get_settings()
configure_logging(level=settings.log_level, fmt=settings.log_format)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("postgres schema ready")
    verify_connectivity()
    init_constraints()
    logger.info("neo4j schema ready")
    yield
    close_driver()


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    description=(
        "A security layer that monitors and governs autonomous AI agents: "
        "their actions, tool calls, delegations, and communication with other agents."
    ),
    lifespan=lifespan,
    # A trailing slash would otherwise redirect: GET /graph/ (an empty agent_id,
    # easily produced by string interpolation in a client) would land on
    # GET /graph and return the entire graph instead of 404ing. For a tool whose
    # job is scoping visibility, silently widening a query is the wrong default.
    redirect_slashes=False,
)


@app.exception_handler(ServiceUnavailable)
async def _neo4j_unavailable(request: Request, exc: ServiceUnavailable):
    logger.error("neo4j unavailable on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=503,
        content={"detail": "The graph database is unreachable. Try again shortly."},
    )


@app.exception_handler(OperationalError)
async def _postgres_unavailable(request: Request, exc: OperationalError):
    logger.error("postgres unavailable on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=503,
        content={"detail": "The event store is unreachable. Try again shortly."},
    )


@app.exception_handler(SQLAlchemyError)
async def _postgres_error(request: Request, exc: SQLAlchemyError):
    logger.exception("database error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "A database error prevented this request from completing."},
    )


@app.exception_handler(Neo4jError)
async def _neo4j_error(request: Request, exc: Neo4jError):
    logger.exception("graph error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "detail": "A graph database error prevented this request from completing."
        },
    )


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    """Never let an internal error reach a caller as a stack trace."""
    logger.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "detail": "An unexpected error occurred. The incident has been logged."
        },
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(agents.router)
app.include_router(events.router)
app.include_router(graph.router)
app.include_router(alerts.router)
app.include_router(delegations.router)
app.include_router(a2a.router)
app.include_router(incidents.router)
app.include_router(forensics.router)


@app.get(
    "/health",
    tags=["meta"],
    response_model=HealthResponse,
    summary="Liveness and dependency health",
    responses={
        503: {
            "model": HealthResponse,
            "description": "At least one backing store is unreachable",
        }
    },
)
def health() -> JSONResponse:
    """Verify that the API can actually reach the stores it depends on.

    Each dependency is reported individually, so a caller can tell *which* one
    is down rather than only that something is. Returns 503 if either is
    unreachable, so an orchestrator can act on it without parsing the body.
    """
    body, healthy = health_report(timeout=settings.health_timeout_seconds)
    body["app"] = settings.app_name
    return JSONResponse(status_code=200 if healthy else 503, content=body)
