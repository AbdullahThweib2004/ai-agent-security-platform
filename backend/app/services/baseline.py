"""On-the-fly behavioural baselines, computed per request from Postgres history.

No persisted profile table: a baseline is always derived from the events that
preceded the one being judged. That keeps the baseline honest (it can never
drift out of sync with the log) at the cost of a query per evaluation, which is
the right trade at MVP volume. The composite index on
``(actor_id, timestamp)`` serves exactly this access pattern.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.event import AgentEvent
from app.services.trust import is_rated

# Metadata keys inspected for a numeric "value" of the action, in priority
# order. The first key present and numeric wins.
VALUE_KEYS = ("amount", "transaction_amount", "value", "total")

# An agent needs at least this much history before its baseline is trusted
# enough to judge against; below it, everything looks novel and every event
# would be an alert.
# Statuses that never contribute to "normal".
_EXCLUDED_FROM_BASELINE = frozenset({"blocked", "suspicious"})


@dataclass
class AgentBaseline:
    """What "normal" looks like for one agent, as of a point in time."""

    agent_id: str
    event_count: int = 0
    known_targets: set[str] = field(default_factory=set)
    known_target_types: set[str] = field(default_factory=set)
    known_action_types: set[str] = field(default_factory=set)
    known_permissions: set[str] = field(default_factory=set)
    values: list[float] = field(default_factory=list)
    first_seen: datetime | None = None
    last_seen: datetime | None = None

    @property
    def is_established(self) -> bool:
        """Whether there is enough history to judge new behaviour against.

        Delegates to ``trust.is_rated`` so this and every other consumer share
        one definition of "unrated" rather than four that must agree.
        """
        return is_rated(self.event_count)

    @property
    def value_min(self) -> float | None:
        return min(self.values) if self.values else None

    @property
    def value_max(self) -> float | None:
        return max(self.values) if self.values else None

    @property
    def value_mean(self) -> float | None:
        return statistics.fmean(self.values) if self.values else None

    @property
    def value_stdev(self) -> float | None:
        return statistics.pstdev(self.values) if len(self.values) > 1 else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "event_count": self.event_count,
            "is_established": self.is_established,
            "known_targets": sorted(self.known_targets),
            "known_target_types": sorted(self.known_target_types),
            "known_action_types": sorted(self.known_action_types),
            "known_permissions": sorted(self.known_permissions),
            "value_range": {
                "min": self.value_min,
                "max": self.value_max,
                "mean": self.value_mean,
                "stdev": self.value_stdev,
                "samples": len(self.values),
            },
            "first_seen": self.first_seen.isoformat() if self.first_seen else None,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
        }


def extract_value(metadata: dict[str, Any] | None) -> float | None:
    """Pull a comparable numeric value out of an event's free-form metadata."""
    if not metadata:
        return None
    for key in VALUE_KEYS:
        if key in metadata:
            raw = metadata[key]
            if isinstance(raw, bool):
                continue
            if isinstance(raw, int | float):
                return float(raw)
            if isinstance(raw, str):
                try:
                    return float(raw.replace(",", "").strip())
                except ValueError:
                    continue
    return None


def compute_baseline(
    session: Session,
    agent_id: str,
    *,
    before: datetime | None = None,
    exclude_event_id: UUID | None = None,
) -> AgentBaseline:
    """Build the baseline for ``agent_id`` from the events it has already taken.

    ``before`` and ``exclude_event_id`` keep the event under evaluation out of
    its own baseline, so an anomalous event can never normalise itself.
    """
    stmt = select(AgentEvent).where(AgentEvent.actor_id == agent_id)
    if before is not None:
        stmt = stmt.where(AgentEvent.timestamp < before)
    if exclude_event_id is not None:
        stmt = stmt.where(AgentEvent.event_id != exclude_event_id)
    stmt = stmt.order_by(AgentEvent.timestamp)

    baseline = AgentBaseline(agent_id=agent_id)
    for event in session.scalars(stmt):
        # A baseline must describe known-good behaviour only. Blocked attempts
        # describe what the agent tried, not what it may do; suspicious events
        # are what we already flagged. Folding either back in would let an
        # agent widen its own definition of normal one anomaly at a time.
        if event.platform_status in _EXCLUDED_FROM_BASELINE:
            continue
        # Nor may an agent widen its definition of normal while contained.
        # Without this, a suspended agent could accrue "clean" history during
        # the suspension and emerge better-rated than it went in.
        if event.actor_suspended:
            continue
        baseline.event_count += 1
        baseline.known_targets.add(event.target_id)
        baseline.known_target_types.add(event.target_type)
        baseline.known_action_types.add(event.action_type)
        baseline.known_permissions.update(event.permissions_used or [])
        value = extract_value(event.event_metadata)
        if value is not None:
            baseline.values.append(value)
        if baseline.first_seen is None:
            baseline.first_seen = event.timestamp
        baseline.last_seen = event.timestamp

    return baseline
