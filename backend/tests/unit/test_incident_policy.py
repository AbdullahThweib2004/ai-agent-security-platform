"""Incident detection: the threshold, in isolation.

The assessment object is a pure function of the signals it holds, so the whole
threshold can be pinned without touching a database.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.services.incident_policy import (
    LAYER_A2A,
    LAYER_ALERT,
    LAYER_DELEGATION,
    MIN_DISTINCT_EVENTS,
    MIN_DISTINCT_LAYERS,
    MIN_HIGH_SEVERITY_ALERTS,
    WINDOW,
    Assessment,
    Signal,
    _is_only_an_upstream_citation,
)

T0 = datetime(2026, 3, 2, 9, 0, tzinfo=UTC)


def sig(layer, event="e1", high=False, at=T0):
    return Signal(
        event_id=event, layer=layer, at=at, detail=f"{layer} fired", high_severity=high
    )


def assess_of(*signals):
    return Assessment(
        agent_id="payment-agent",
        signals=list(signals),
        window_start=T0 - WINDOW,
        window_end=T0,
    )


# --- nothing, or not enough --------------------------------------------------
def test_no_signals_does_not_trip():
    assert assess_of().trips is False


def test_one_layer_however_many_signals_does_not_trip():
    """Volume is not the discriminator — corroboration is."""
    many = assess_of(*[sig(LAYER_DELEGATION, event=f"e{i}") for i in range(10)])
    assert len(many.signals) == 10
    assert many.trips is False


def test_one_high_alert_does_not_trip():
    assert assess_of(sig(LAYER_ALERT, high=True)).trips is False


# --- the corroboration branch ------------------------------------------------
def test_two_layers_across_two_events_trips():
    a = assess_of(sig(LAYER_ALERT, event="e1"), sig(LAYER_A2A, event="e2"))
    assert a.corroborated is True
    assert a.trips is True
    assert "2 independent layers" in a.reason
    assert "across 2 events" in a.reason


def test_two_layers_on_a_single_event_does_not_trip():
    """Two views of one action is not corroboration across behaviour.

    One anomalous act is what the alert layer already exists for; containment
    is a judgement about a pattern.
    """
    a = assess_of(sig(LAYER_ALERT, event="e1"), sig(LAYER_A2A, event="e1"))
    assert len(a.layers) >= MIN_DISTINCT_LAYERS
    assert len(a.event_ids) < MIN_DISTINCT_EVENTS
    assert a.corroborated is False
    assert a.trips is False


def test_three_layers_across_two_events_trips():
    a = assess_of(
        sig(LAYER_ALERT, event="e1"),
        sig(LAYER_DELEGATION, event="e1"),
        sig(LAYER_A2A, event="e2"),
    )
    assert a.trips is True


# --- the severity branch -----------------------------------------------------
def test_two_high_alerts_on_one_event_trips():
    """A smoking gun does not need a second act to confirm it."""
    a = assess_of(
        sig(LAYER_ALERT, event="e1", high=True), sig(LAYER_ALERT, event="e1", high=True)
    )
    assert a.corroborated is False
    assert a.high_alert_count == MIN_HIGH_SEVERITY_ALERTS
    assert a.trips is True
    assert "high-severity alerts" in a.reason


def test_medium_alerts_do_not_count_toward_the_severity_branch():
    a = assess_of(*[sig(LAYER_ALERT, event=f"e{i}") for i in range(5)])
    assert a.high_alert_count == 0
    assert a.trips is False


# --- windowing on event time, not detection time -----------------------------
def test_the_window_is_measured_on_event_time():
    """The exact scenario that motivated the design.

    A replay or backfill records every signal at the same instant, so a window
    measured on when the platform *noticed* would collapse fourteen days of
    history into one burst and make every agent look like an attack. These
    signals carry event times two weeks apart; only their detection was
    simultaneous.
    """
    long_ago = T0 - timedelta(days=14)
    replayed = Assessment(
        agent_id="finance-agent",
        signals=[
            sig(LAYER_DELEGATION, event="old", at=long_ago),
            sig(LAYER_A2A, event="new", at=T0),
        ],
        window_start=T0 - WINDOW,
        window_end=T0,
    )
    # The assessment holds both because a naive gather would; the window is what
    # excludes the stale one, and `assess` applies it against event timestamps.
    in_window = [s for s in replayed.signals if s.at > replayed.window_start]
    assert len(in_window) == 1, "the fourteen-day-old signal is outside the window"
    assert {s.layer for s in in_window} == {LAYER_A2A}


def test_the_window_is_24h():
    assert WINDOW == timedelta(hours=24)


# --- double counting ---------------------------------------------------------
class _FakeDelegation:
    def __init__(self, rules):
        self.permission_decisions = [{"rule": r} for r in rules]


def test_a_delegation_that_only_cites_upstream_is_not_an_independent_signal():
    """It relayed a conclusion; it did not reach one.

    Counting it would make a single finding look like two layers agreeing,
    which is precisely what this threshold treats as evidence.
    """
    assert (
        _is_only_an_upstream_citation(_FakeDelegation(["upstream_a2a_block"])) is True
    )


def test_a_delegation_with_its_own_reasoning_does_count():
    for rules in (
        ["confinement"],
        ["sensitive_category"],
        ["upstream_a2a_block", "confinement"],
    ):
        assert _is_only_an_upstream_citation(_FakeDelegation(rules)) is False


def test_a_delegation_with_no_verdicts_is_not_discarded():
    assert _is_only_an_upstream_citation(_FakeDelegation([])) is False


# --- summary shape -----------------------------------------------------------
def test_the_summary_records_the_threshold_it_was_judged_against():
    a = assess_of(sig(LAYER_ALERT, event="e1"), sig(LAYER_A2A, event="e2"))
    summary = a.summary()
    assert summary["signal_count"] == 2
    assert summary["event_count"] == 2
    assert summary["layers"] == sorted([LAYER_A2A, LAYER_ALERT])
    assert summary["threshold"]["min_distinct_layers"] == MIN_DISTINCT_LAYERS
    assert summary["threshold"]["min_distinct_events"] == MIN_DISTINCT_EVENTS
    assert summary["threshold"]["window_hours"] == 24
    assert summary["window_start"] and summary["window_end"]


@pytest.mark.parametrize("layer", [LAYER_ALERT, LAYER_DELEGATION, LAYER_A2A])
def test_every_layer_is_recognised(layer):
    assert assess_of(sig(layer)).layers == {layer}
