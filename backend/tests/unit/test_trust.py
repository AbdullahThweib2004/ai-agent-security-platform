"""The shared 'unrated' determination.

Four layers consume this — dashboard health, anomaly evaluation, delegation
authority, and (next) interaction policy. These tests pin the single definition
they all agree on.
"""

from __future__ import annotations

import pytest

from app.services.baseline import AgentBaseline
from app.services.graph_service import _health_of
from app.services.trust import MIN_BASELINE_EVENTS, is_rated, unrated_reason


@pytest.mark.parametrize("count", range(MIN_BASELINE_EVENTS))
def test_below_the_threshold_is_unrated(count):
    assert is_rated(count) is False


def test_never_seen_at_all_is_the_same_case_as_too_little_history():
    """Zero events is unrated by the same threshold — one axis, not two rules."""
    assert is_rated(0) is False
    assert is_rated(MIN_BASELINE_EVENTS - 1) is False


@pytest.mark.parametrize("count", [MIN_BASELINE_EVENTS, MIN_BASELINE_EVENTS + 1, 500])
def test_at_or_above_the_threshold_is_rated(count):
    assert is_rated(count) is True


def test_unrated_reason_states_the_fact_and_the_consequence():
    reason = unrated_reason("external-agent-x", 1, "nothing may be delegated to it")
    assert "external-agent-x is unrated" in reason
    assert "only 1 clean event(s) on record" in reason
    assert reason.endswith("nothing may be delegated to it")


# --- every consumer agrees with the shared definition ------------------------
@pytest.mark.parametrize("count", [0, 1, 2, 3, 4, 10])
def test_baseline_is_established_matches_is_rated(count):
    baseline = AgentBaseline(agent_id="a", event_count=count)
    assert baseline.is_established is is_rated(count)


@pytest.mark.parametrize("count", [0, 1, 2, 3, 4, 10])
def test_dashboard_health_matches_is_rated(count):
    node = {"id": "a", "type": "agent", "suspicious_count": 0}
    health = _health_of(node, alert_count=0, clean_count=count)
    assert (health == "unrated") is (not is_rated(count))


def test_the_three_consumers_cannot_disagree():
    """A regression guard on the extraction itself.

    If any consumer reintroduced its own threshold, one of these would diverge
    at the boundary.
    """
    boundary = MIN_BASELINE_EVENTS
    node = {"id": "a", "type": "agent", "suspicious_count": 0}

    assert is_rated(boundary - 1) is False
    assert AgentBaseline(agent_id="a", event_count=boundary - 1).is_established is False
    assert _health_of(node, 0, boundary - 1) == "unrated"

    assert is_rated(boundary) is True
    assert AgentBaseline(agent_id="a", event_count=boundary).is_established is True
    assert _health_of(node, 0, boundary) == "healthy"
