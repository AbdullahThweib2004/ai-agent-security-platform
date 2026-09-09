"""Dependency health checks.

A health endpoint that only proves the process is running is worse than none:
it reports green while every request fails. These checks issue a real query
against each store, so "healthy" means the thing the API actually needs is
reachable and answering.

Each probe runs on a daemon thread with a wall-clock deadline. A store that has
stopped answering usually does not refuse the connection — it hangs — and a
health check that hangs with it is exactly what takes a load balancer down with
the dependency. The deadline is what makes a hang report as a failure, and the
thread being a daemon is what stops an abandoned probe from holding the process
open at shutdown.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# Deliberately short. This endpoint is polled by orchestrators on a tight
# interval; a slow answer is itself a failure signal.
DEFAULT_TIMEOUT_SECONDS = 2.0


@dataclass
class DependencyStatus:
    name: str
    ok: bool
    detail: str
    latency_ms: float

    def as_value(self) -> str:
        """The per-dependency field in the response body."""
        return "ok" if self.ok else f"error: {self.detail}"


def _timed(name: str, probe, timeout: float) -> DependencyStatus:
    """Run ``probe`` on a daemon thread with a deadline.

    The thread must be a daemon. A pooled worker (ThreadPoolExecutor) is joined
    by an interpreter-exit hook, so a probe still blocked on a hung store would
    hold the whole process open at shutdown — delaying a rolling deploy for
    exactly as long as the dependency stays wedged. A daemon thread is abandoned
    instead: the request returns on the deadline and the process can still exit.
    """
    started = time.perf_counter()
    outcome: dict = {}
    finished = threading.Event()

    def run() -> None:
        try:
            probe()
            outcome["ok"] = True
        except Exception as exc:  # noqa: BLE001 - any failure is a failed check
            outcome["error"] = exc
        finally:
            finished.set()

    thread = threading.Thread(target=run, name=f"healthcheck-{name}", daemon=True)
    thread.start()
    completed = finished.wait(timeout)
    elapsed = (time.perf_counter() - started) * 1000

    if not completed:
        return DependencyStatus(name, False, f"timed out after {timeout:g}s", elapsed)
    if "error" in outcome:
        return DependencyStatus(name, False, _summarise(outcome["error"]), elapsed)
    return DependencyStatus(name, True, "ok", elapsed)


def _summarise(exc: Exception) -> str:
    """A short, safe description of a failure.

    Driver exceptions can carry DSNs and credentials in their string form, so
    only the exception type and its first line are surfaced.
    """
    first_line = str(exc).strip().splitlines()[0] if str(exc).strip() else ""
    summary = (
        f"{type(exc).__name__}: {first_line}" if first_line else type(exc).__name__
    )
    return summary[:200]


def check_postgres(
    engine: Engine | None = None, timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> DependencyStatus:
    """Round-trip a trivial query against Postgres."""
    if engine is None:
        from app.db.postgres import engine as default_engine

        engine = default_engine

    def probe() -> None:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))

    return _timed("postgres", probe, timeout)


def check_neo4j(
    driver=None, timeout: float = DEFAULT_TIMEOUT_SECONDS
) -> DependencyStatus:
    """Round-trip a trivial Cypher statement against Neo4j."""
    if driver is None:
        from app.db.neo4j import get_driver

        driver = get_driver()

    def probe() -> None:
        with driver.session() as session:
            session.run("RETURN 1 AS ok").single()

    return _timed("neo4j", probe, timeout)


def health_report(timeout: float = DEFAULT_TIMEOUT_SECONDS) -> tuple[dict, bool]:
    """Check every dependency. Returns (body, all_healthy).

    Both stores are always probed, even when the first has already failed: an
    operator needs to know whether one thing broke or everything did.
    """
    postgres = check_postgres(timeout=timeout)
    neo4j = check_neo4j(timeout=timeout)
    dependencies = [postgres, neo4j]

    healthy = all(d.ok for d in dependencies)
    body = {
        "status": "healthy" if healthy else "degraded",
        "postgres": postgres.as_value(),
        "neo4j": neo4j.as_value(),
        "latency_ms": {d.name: round(d.latency_ms, 1) for d in dependencies},
    }

    if not healthy:
        failed = [d.name for d in dependencies if not d.ok]
        logger.error(
            "health check failed",
            extra={
                "event": "health.check.failed",
                "failed_dependencies": failed,
                "postgres_ok": postgres.ok,
                "neo4j_ok": neo4j.ok,
                "postgres_detail": postgres.detail,
                "neo4j_detail": neo4j.detail,
            },
        )

    return body, healthy
