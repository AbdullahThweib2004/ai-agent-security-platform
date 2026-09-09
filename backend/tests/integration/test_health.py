"""GET /health must prove the stores are reachable, not that the process is up.

A health endpoint that reports green while every request fails is worse than
having none, so these tests break connectivity for real — pointing a driver at a
dead port — rather than mocking the check away.
"""

from __future__ import annotations

import time

import pytest
from neo4j import GraphDatabase
from sqlalchemy import create_engine

from app.services.health import check_neo4j, check_postgres, health_report

pytestmark = pytest.mark.integration

# Nothing listens here; connections are refused or time out.
DEAD_POSTGRES = "postgresql+psycopg://nobody:nobody@127.0.0.1:1/nothing"
DEAD_NEO4J = "bolt://127.0.0.1:1"


def test_health_is_200_and_reports_each_dependency(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "healthy"
    assert body["postgres"] == "ok"
    assert body["neo4j"] == "ok"
    assert set(body["latency_ms"]) == {"postgres", "neo4j"}


def test_health_probes_both_stores_on_every_call(client, monkeypatch):
    """If it were not really querying, breaking the stores would not matter."""
    import app.services.health as health_module

    calls = []
    real_pg, real_neo = health_module.check_postgres, health_module.check_neo4j

    def counting_pg(**kwargs):
        calls.append("postgres")
        return real_pg(**kwargs)

    def counting_neo(**kwargs):
        calls.append("neo4j")
        return real_neo(**kwargs)

    monkeypatch.setattr(health_module, "check_postgres", counting_pg)
    monkeypatch.setattr(health_module, "check_neo4j", counting_neo)

    assert client.get("/health").status_code == 200
    assert sorted(calls) == ["neo4j", "postgres"]


# --- real connectivity failures ---------------------------------------------
def test_postgres_probe_reports_a_dead_server():
    engine = create_engine(DEAD_POSTGRES, connect_args={"connect_timeout": 1})
    status = check_postgres(engine=engine, timeout=5.0)
    assert status.ok is False
    assert status.name == "postgres"
    assert status.detail
    assert status.as_value().startswith("error: ")


def test_neo4j_probe_reports_a_dead_server():
    driver = GraphDatabase.driver(
        DEAD_NEO4J, auth=("neo4j", "nope"), connection_timeout=1
    )
    try:
        status = check_neo4j(driver=driver, timeout=5.0)
    finally:
        driver.close()
    assert status.ok is False
    assert status.name == "neo4j"
    assert status.as_value().startswith("error: ")


def test_health_returns_503_when_postgres_is_unreachable(client, monkeypatch):
    engine = create_engine(DEAD_POSTGRES, connect_args={"connect_timeout": 1})
    monkeypatch.setattr("app.db.postgres.engine", engine)

    response = client.get("/health")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["postgres"].startswith("error: ")
    # the healthy dependency is still probed and still reported
    assert body["neo4j"] == "ok"


def test_health_returns_503_when_neo4j_is_unreachable(client, monkeypatch):
    driver = GraphDatabase.driver(
        DEAD_NEO4J, auth=("neo4j", "nope"), connection_timeout=1
    )
    monkeypatch.setattr("app.db.neo4j.get_driver", lambda: driver)
    try:
        response = client.get("/health")
    finally:
        driver.close()

    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["neo4j"].startswith("error: ")
    assert body["postgres"] == "ok"


def test_health_identifies_both_failures_separately(client, monkeypatch):
    """An operator needs to know whether one thing broke or everything did."""
    engine = create_engine(DEAD_POSTGRES, connect_args={"connect_timeout": 1})
    driver = GraphDatabase.driver(
        DEAD_NEO4J, auth=("neo4j", "nope"), connection_timeout=1
    )
    monkeypatch.setattr("app.db.postgres.engine", engine)
    monkeypatch.setattr("app.db.neo4j.get_driver", lambda: driver)
    try:
        response = client.get("/health")
    finally:
        driver.close()

    assert response.status_code == 503
    body = response.json()
    assert body["postgres"].startswith("error: ")
    assert body["neo4j"].startswith("error: ")


# --- hangs, not just refusals ------------------------------------------------
def test_a_hanging_dependency_fails_fast_instead_of_hanging(client, monkeypatch):
    """A store that stops answering usually hangs rather than refusing.

    A health check that hangs with it takes the load balancer down too, so the
    deadline is the thing being tested here.
    """
    import app.services.health as health_module

    def hang(*args, **kwargs):
        time.sleep(30)

    monkeypatch.setattr(
        health_module,
        "check_postgres",
        lambda timeout=2.0: health_module._timed("postgres", hang, 0.5),
    )

    started = time.perf_counter()
    response = client.get("/health")
    elapsed = time.perf_counter() - started

    assert response.status_code == 503
    assert "timed out" in response.json()["postgres"]
    assert elapsed < 10, f"health check should not hang; took {elapsed:.1f}s"


def test_timeout_detail_names_the_deadline():
    def hang():
        time.sleep(30)

    from app.services.health import _timed

    status = _timed("postgres", hang, 0.3)
    assert status.ok is False
    assert "timed out after 0.3s" in status.detail


def test_probe_failures_do_not_leak_credentials():
    """Driver errors can stringify a DSN; the summary must stay short and safe."""
    engine = create_engine(
        "postgresql+psycopg://secretuser:supersecret@127.0.0.1:1/nothing",
        connect_args={"connect_timeout": 1},
    )
    status = check_postgres(engine=engine, timeout=5.0)
    assert "supersecret" not in status.detail
    assert len(status.detail) <= 200


def test_health_report_is_a_pure_function_of_the_stores():
    body, healthy = health_report()
    assert healthy is True
    assert body["status"] == "healthy"
    assert body["postgres"] == "ok" and body["neo4j"] == "ok"
