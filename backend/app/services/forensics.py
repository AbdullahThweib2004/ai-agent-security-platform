"""Forensic reconstruction of an event chain.

Given any event in an incident, rebuild the whole causal tree it belongs to:
walk backward through ``parent_event_id`` to the root cause, then forward
through every event descended from that root. Branches count — an investigator
asking about one leg of an incident needs to see the other legs too.

Both walks are recursive CTEs, so the whole chain costs two queries regardless
of its depth.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.event import AgentEvent, Alert
from app.schemas.event import AlertOut, event_to_out
from app.schemas.forensics import TimelineEntry, TimelineResponse

# Guards the recursion. A cycle cannot normally form (a parent must already
# exist before a child can reference it) but the log is evidence, and evidence
# should not be able to hang the investigation tool.
MAX_CHAIN_DEPTH = 100

_ANCESTORS_SQL = text(
    """
    WITH RECURSIVE ancestors AS (
        SELECT event_id, parent_event_id, 0 AS hops
        FROM agent_events
        WHERE event_id = :event_id
      UNION ALL
        SELECT e.event_id, e.parent_event_id, a.hops + 1
        FROM agent_events e
        JOIN ancestors a ON e.event_id = a.parent_event_id
        WHERE a.hops < :max_depth
    )
    SELECT event_id, hops FROM ancestors ORDER BY hops
    """
)

_SUBTREE_SQL = text(
    """
    WITH RECURSIVE subtree AS (
        SELECT event_id, 0 AS depth
        FROM agent_events
        WHERE event_id = :root_id
      UNION ALL
        SELECT e.event_id, s.depth + 1
        FROM agent_events e
        JOIN subtree s ON e.parent_event_id = s.event_id
        WHERE s.depth < :max_depth
    )
    SELECT event_id, depth FROM subtree
    """
)


def build_timeline(session: Session, event_id: UUID) -> TimelineResponse | None:
    """Reconstruct the full chain containing ``event_id``."""
    if session.get(AgentEvent, event_id) is None:
        return None

    ancestor_rows = session.execute(
        _ANCESTORS_SQL, {"event_id": event_id, "max_depth": MAX_CHAIN_DEPTH}
    ).all()
    ancestor_ids = {row.event_id for row in ancestor_rows} - {event_id}
    root_id = ancestor_rows[-1].event_id if ancestor_rows else event_id

    subtree_rows = session.execute(
        _SUBTREE_SQL, {"root_id": root_id, "max_depth": MAX_CHAIN_DEPTH}
    ).all()
    depth_by_id = {row.event_id: row.depth for row in subtree_rows}

    # Everything below the event that was asked about, for the descendant tag.
    descendant_rows = session.execute(
        _SUBTREE_SQL, {"root_id": event_id, "max_depth": MAX_CHAIN_DEPTH}
    ).all()
    descendant_ids = {row.event_id for row in descendant_rows} - {event_id}

    all_ids = set(depth_by_id) | ancestor_ids | descendant_ids | {event_id}

    events = list(
        session.scalars(
            select(AgentEvent)
            .where(AgentEvent.event_id.in_(all_ids))
            .order_by(AgentEvent.timestamp)
        )
    )
    if not events:
        return None

    alerts_by_event: dict[UUID, list[Alert]] = {}
    for alert in session.scalars(select(Alert).where(Alert.event_id.in_(all_ids))):
        alerts_by_event.setdefault(alert.event_id, []).append(alert)

    def relation_of(eid: UUID) -> str:
        if eid == event_id:
            return "self"
        if eid in ancestor_ids:
            return "ancestor"
        if eid in descendant_ids:
            return "descendant"
        return "related"

    entries = [
        TimelineEntry(
            event=event_to_out(e),
            depth=depth_by_id.get(e.event_id, 0),
            relation=relation_of(e.event_id),
            alerts=[
                AlertOut.model_validate(a) for a in alerts_by_event.get(e.event_id, [])
            ],
        )
        for e in events
    ]

    participants = sorted({e.actor_id for e in events} | {e.target_id for e in events})

    return TimelineResponse(
        requested_event_id=event_id,
        root_event_id=root_id,
        started_at=events[0].timestamp,
        ended_at=events[-1].timestamp,
        event_count=len(entries),
        alert_count=sum(len(e.alerts) for e in entries),
        participants=participants,
        entries=entries,
    )
