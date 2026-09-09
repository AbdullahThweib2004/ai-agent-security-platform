"""Each anomaly rule, in isolation.

These touch no database: a rule is a pure function of (event, baseline), and
testing it that way pins the decision boundary precisely.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.anomaly import (
    STDEV_MULTIPLIER,
    VALUE_TOLERANCE,
    _rule_new_permission,
    _rule_unseen_counterparty,
    _rule_value_excursion,
    evaluate,
)
from app.services.baseline import AgentBaseline
from app.services.trust import MIN_BASELINE_EVENTS


def make_event(**kw):
    defaults = {
        "actor_id": "payment-agent",
        "target_id": "bank-api",
        "target_type": "api",
        "action_type": "api_call",
        "permissions_used": ["bank:transfer"],
        "event_metadata": {},
        "platform_status": "allowed",
    }
    defaults.update(kw)
    return SimpleNamespace(**defaults)


def make_baseline(**kw):
    defaults = {
        "agent_id": "payment-agent",
        "event_count": 10,
        "known_targets": {"bank-api"},
        "known_target_types": {"api"},
        "known_action_types": {"api_call"},
        "known_permissions": {"bank:transfer"},
        "values": [1000.0, 1200.0, 900.0, 1100.0],
    }
    defaults.update(kw)
    return AgentBaseline(**defaults)


# --- unseen_counterparty ----------------------------------------------------
def test_known_counterparty_does_not_fire():
    assert _rule_unseen_counterparty(make_event(), make_baseline()) is None


def test_unseen_counterparty_fires():
    finding = _rule_unseen_counterparty(
        make_event(target_id="external-agent-x", target_type="agent"), make_baseline()
    )
    assert finding is not None
    assert finding.rule_name == "unseen_counterparty"
    assert finding.details["target_id"] == "external-agent-x"
    assert finding.details["known_targets"] == ["bank-api"]


def test_first_contact_with_an_agent_is_high_severity():
    """Reaching a new agent is lateral movement; a new tool is just a new tool."""
    agent = _rule_unseen_counterparty(
        make_event(target_id="external-agent-x", target_type="agent"), make_baseline()
    )
    tool = _rule_unseen_counterparty(
        make_event(target_id="new-tool", target_type="tool"), make_baseline()
    )
    assert agent.severity == "high"
    assert tool.severity == "medium"


# --- value_excursion --------------------------------------------------------
def test_value_inside_range_does_not_fire():
    event = make_event(event_metadata={"amount": 1150.0})
    assert _rule_value_excursion(event, make_baseline()) is None


def test_value_excursion_fires_far_above_the_maximum():
    event = make_event(event_metadata={"amount": 880_000.0})
    finding = _rule_value_excursion(event, make_baseline())
    assert finding is not None
    assert finding.severity == "high"
    assert finding.details["observed_value"] == 880_000.0
    assert finding.details["baseline_max"] == 1200.0


@pytest.mark.parametrize(
    "amount, should_fire",
    [
        (1200.0, False),  # exactly the historical max
        (1799.0, False),  # just under max * tolerance
        (1801.0, True),  # just over it
    ],
)
def test_value_excursion_boundary(amount, should_fire):
    """The threshold is max * VALUE_TOLERANCE, and it is strict."""
    baseline = make_baseline(values=[1200.0, 1200.0, 1200.0])  # zero stdev
    assert baseline.value_max * VALUE_TOLERANCE == pytest.approx(1800.0)
    finding = _rule_value_excursion(
        make_event(event_metadata={"amount": amount}), baseline
    )
    assert (finding is not None) is should_fire


def test_tight_distribution_uses_the_sigma_threshold():
    """A stable agent can be violated well below max * tolerance."""
    baseline = make_baseline(values=[1000.0] * 9 + [1010.0])
    sigma = baseline.value_mean + STDEV_MULTIPLIER * baseline.value_stdev
    assert sigma < baseline.value_max * VALUE_TOLERANCE
    finding = _rule_value_excursion(
        make_event(event_metadata={"amount": sigma + 50}), baseline
    )
    assert finding is not None
    assert finding.details["trigger"] == "mean_plus_3_stdev"


def test_value_excursion_needs_a_numeric_value():
    """Events carrying no comparable value are not judged on value."""
    assert (
        _rule_value_excursion(
            make_event(event_metadata={"rows": 48_500}), make_baseline()
        )
        is None
    )
    assert _rule_value_excursion(make_event(event_metadata={}), make_baseline()) is None


def test_value_excursion_needs_value_history():
    baseline = make_baseline(values=[])
    assert (
        _rule_value_excursion(make_event(event_metadata={"amount": 1e9}), baseline)
        is None
    )


# --- new_permission ---------------------------------------------------------
def test_known_permission_does_not_fire():
    assert _rule_new_permission(make_event(), make_baseline()) is None


def test_new_permission_fires_and_lists_only_the_novel_ones():
    finding = _rule_new_permission(
        make_event(permissions_used=["bank:transfer", "bank:admin"]), make_baseline()
    )
    assert finding is not None
    assert finding.details["new_permissions"] == ["bank:admin"]
    assert finding.severity == "medium"


def test_several_new_permissions_at_once_is_high_severity():
    finding = _rule_new_permission(
        make_event(permissions_used=["bank:admin", "bank:export"]), make_baseline()
    )
    assert finding.severity == "high"


def test_no_permissions_used_does_not_fire():
    assert (
        _rule_new_permission(make_event(permissions_used=[]), make_baseline()) is None
    )


# --- evaluate() -------------------------------------------------------------
def test_cold_start_suppresses_every_rule():
    """Below the threshold nothing is judged, however obvious it looks."""
    baseline = make_baseline(event_count=MIN_BASELINE_EVENTS - 1)
    assert not baseline.is_established
    event = make_event(
        target_id="external-agent-x",
        target_type="agent",
        permissions_used=["bank:admin"],
        event_metadata={"amount": 999_999.0},
    )
    assert evaluate(event, baseline) == []


def test_established_baseline_reports_every_rule_that_fires_worst_first():
    baseline = make_baseline(event_count=MIN_BASELINE_EVENTS)
    event = make_event(
        target_id="external-agent-x",
        target_type="agent",
        permissions_used=["bank:admin"],
        event_metadata={"amount": 880_000.0},
    )
    findings = evaluate(event, baseline)
    assert {f.rule_name for f in findings} == {
        "unseen_counterparty",
        "value_excursion",
        "new_permission",
    }
    severities = [f.severity for f in findings]
    assert severities == sorted(severities, key=lambda s: {"high": 0, "medium": 1}[s])
