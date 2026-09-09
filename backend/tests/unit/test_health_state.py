"""Agent health is three states, not two.

Reporting an agent as "healthy" when the rules have never been able to judge it
is a false reassurance — it is exactly the freshly-introduced agent that most
warrants a second look.
"""

from __future__ import annotations

import pytest

from app.services.graph_service import _health_of
from app.services.trust import MIN_BASELINE_EVENTS


def node(node_type="agent", suspicious_count=0):
    return {"id": "x", "type": node_type, "suspicious_count": suspicious_count}


def test_established_and_quiet_is_healthy():
    assert _health_of(node(), alert_count=0, clean_count=50) == "healthy"


def test_any_alert_makes_it_suspicious():
    assert _health_of(node(), alert_count=1, clean_count=50) == "suspicious"


def test_a_suspicious_event_makes_it_suspicious_even_with_no_alert_row():
    assert (
        _health_of(node(suspicious_count=1), alert_count=0, clean_count=50)
        == "suspicious"
    )


@pytest.mark.parametrize("clean", range(MIN_BASELINE_EVENTS))
def test_below_the_threshold_an_agent_is_unrated_not_healthy(clean):
    assert _health_of(node(), alert_count=0, clean_count=clean) == "unrated"


def test_at_the_threshold_it_becomes_healthy():
    assert (
        _health_of(node(), alert_count=0, clean_count=MIN_BASELINE_EVENTS) == "healthy"
    )


def test_suspicion_outranks_unrated():
    """A brand-new agent that already tripped a rule is not merely 'unrated'."""
    assert _health_of(node(), alert_count=2, clean_count=0) == "suspicious"


@pytest.mark.parametrize("entity", ["tool", "api", "database", "user"])
def test_only_agents_are_graded_unrated(entity):
    """A passive API initiates nothing; 'unrated' would be meaningless for it."""
    assert _health_of(node(entity), alert_count=0, clean_count=0) == "healthy"
