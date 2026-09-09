"""Re-project events that reached Postgres but never reached the graph.

The ingest path stages its two writes: the graph transaction is prepared, then
Postgres commits, then the graph commits. One window survives that ordering —
Postgres committed and the graph commit then failed. The event is durable and
correct, but invisible in the behavior graph.

Because the projection is MERGE-based, replaying it is safe: re-projecting an
event that is already in the graph converges rather than duplicating. This
module is what makes the self-heal an actual mechanism instead of a hope.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.neo4j import get_driver, project_event
from app.models.event import AgentEvent

logger = logging.getLogger(__name__)


def find_unprojected(session: Session, limit: int = 500) -> list[AgentEvent]:
    """Events durable in Postgres but not yet confirmed in the graph."""
    stmt = (
        select(AgentEvent)
        .where(AgentEvent.graph_projected.is_(False))
        .order_by(AgentEvent.timestamp)
        .limit(limit)
    )
    return list(session.scalars(stmt))


def reconcile(session: Session, limit: int = 500) -> dict:
    """Replay every unprojected event into the graph.

    Each event is committed independently: one poisoned event must not block
    the rest of the backlog from healing.
    """
    pending = find_unprojected(session, limit=limit)
    repaired: list[str] = []
    failed: list[dict] = []

    driver = get_driver()
    for event in pending:
        try:
            with driver.session() as neo_session:
                tx = neo_session.begin_transaction()
                try:
                    project_event(
                        event_id=str(event.event_id),
                        timestamp=event.timestamp,
                        actor_type=event.actor_type,
                        actor_id=event.actor_id,
                        target_type=event.target_type,
                        target_id=event.target_id,
                        action_type=event.action_type,
                        permissions_used=list(event.permissions_used or []),
                        platform_status=event.platform_status,
                        tx=tx,
                    )
                    tx.commit()
                except Exception:
                    tx.rollback()
                    raise
            event.graph_projected = True
            session.commit()
            repaired.append(str(event.event_id))
        except Exception as exc:
            session.rollback()
            logger.exception(
                "reconcile failed for event %s",
                event.event_id,
                extra={"event_id": str(event.event_id)},
            )
            failed.append({"event_id": str(event.event_id), "error": str(exc)})

    return {
        "pending": len(pending),
        "repaired": len(repaired),
        "failed": len(failed),
        "repaired_event_ids": repaired,
        "failures": failed,
    }
