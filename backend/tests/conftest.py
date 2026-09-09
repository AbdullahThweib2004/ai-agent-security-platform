"""Shared fixtures.

Integration tests run against a real Postgres and a real Neo4j — the whole point
of the ingest path is that two stores stay consistent, and a mock cannot fail in
the ways that matters. The stack comes from docker-compose.test.yml (or CI
services); both point the same env vars at throwaway instances.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from app.db.neo4j import close_driver, get_driver, init_constraints
from app.db.postgres import SessionLocal, engine, init_db
from app.main import app
from app.models.base import Base

BASE_TIME = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)


@pytest.fixture(scope="session", autouse=True)
def _schema():
    """Rebuild the schema from the models on every run.

    The test databases are throwaway, and ``create_all`` will not alter an
    existing table — so dropping first is what keeps the suite honest when a
    column is added or changed.
    """
    import app.models  # noqa: F401  (registers every model on Base)

    Base.metadata.drop_all(bind=engine)
    init_db()
    init_constraints()
    yield
    close_driver()


@pytest.fixture(autouse=True)
def _clean_stores():
    """Every test starts from empty stores, in both databases."""
    with SessionLocal() as session:
        session.execute(text("TRUNCATE agent_events, alerts CASCADE"))
        session.commit()
    with get_driver().session() as neo:
        neo.run("MATCH (n) DETACH DELETE n")
    yield


@pytest.fixture
def session():
    with SessionLocal() as s:
        yield s


@pytest.fixture(scope="session")
def client():
    # raise_server_exceptions=False so an unhandled error is returned as the 500
    # a real caller would receive, rather than being re-raised into the test.
    # That is the only way to assert we never leak a stack trace.
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def neo():
    with get_driver().session() as s:
        yield s


# --- helpers ----------------------------------------------------------------
def event_payload(**overrides) -> dict:
    """A valid minimal event, overridable field by field."""
    payload = {
        "actor_type": "agent",
        "actor_id": "finance-agent",
        "target_type": "api",
        "target_id": "bank-api",
        "action_type": "api_call",
        "permissions_used": ["bank:transfer"],
        "metadata": {},
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def post_event(client):
    """Ingest an event, asserting it was accepted, and return the response body."""

    def _post(*, expect=201, **overrides):
        response = client.post("/events", json=event_payload(**overrides))
        assert response.status_code == expect, response.text
        return response.json()

    return _post


@pytest.fixture
def seed_baseline(post_event):
    """Give an agent enough clean history that the rules will judge it.

    Returns the amounts used, so tests can assert against the range they imply.
    """

    def _seed(
        actor_id="payment-agent",
        target_id="bank-api",
        target_type="api",
        permissions=("bank:transfer",),
        amounts=(1000.0, 1200.0, 900.0, 1100.0),
        start=BASE_TIME,
    ):
        for i, amount in enumerate(amounts):
            post_event(
                actor_id=actor_id,
                target_id=target_id,
                target_type=target_type,
                permissions_used=list(permissions),
                metadata={"amount": amount},
                timestamp=(start + timedelta(minutes=i)).isoformat(),
            )
        return list(amounts)

    return _seed


def new_uuid() -> str:
    return str(uuid.uuid4())
