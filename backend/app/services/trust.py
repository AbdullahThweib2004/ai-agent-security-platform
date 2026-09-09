"""Whether an agent has enough history to be judged — the single definition.

"Unrated" appears in several places: an agent's health on the dashboard, whether
the anomaly rules will judge an event, whether a delegate may receive authority,
and whether an interaction is permitted at all. Those are four consumers of one
fact, and each one that re-derived it was a chance for the definition to drift.

This module owns the threshold and the predicate. Nothing here touches the
database or a baseline object, so every layer can consume it without a cycle.

Note that "never seen at all" is not a separate case. An agent with zero events
is unrated by the same threshold that covers an agent with two — one axis, one
check, rather than two rules that would have to agree with each other.
"""

from __future__ import annotations

# An agent needs at least this many clean events before its behaviour is
# established enough to judge against, or to trust with delegated authority.
# Below it, everything looks novel and every event would raise an alert.
MIN_BASELINE_EVENTS = 3


def is_rated(clean_event_count: int) -> bool:
    """Whether an agent has enough clean history to be judged.

    ``clean_event_count`` counts only events that contribute to a baseline —
    blocked and suspicious ones are excluded upstream, so an agent cannot earn a
    rating through behaviour that was refused or flagged.
    """
    return clean_event_count >= MIN_BASELINE_EVENTS


def unrated_reason(agent_id: str, clean_event_count: int, consequence: str) -> str:
    """The shared phrasing for refusing something because an agent is unrated.

    Every layer that blocks on this states the same fact the same way, so an
    analyst reading a delegation verdict and an interaction verdict sees one
    explanation rather than two that merely resemble each other.
    """
    return (
        f"{agent_id} is unrated — only {clean_event_count} clean event(s) on "
        f"record, {consequence}"
    )
