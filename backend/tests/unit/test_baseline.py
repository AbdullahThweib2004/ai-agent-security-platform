"""Baseline computation: what counts as normal, and what is excluded from it."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.models.event import AgentEvent
from app.services.baseline import (
    MIN_BASELINE_EVENTS,
    compute_baseline,
    extract_value,
)
from tests.conftest import BASE_TIME


def add_event(session, **kw):
    defaults = {
        "actor_type": "agent",
        "actor_id": "payment-agent",
        "target_type": "api",
        "target_id": "bank-api",
        "action_type": "api_call",
        "permissions_used": ["bank:transfer"],
        "reported_status": "allowed",
        "platform_status": "allowed",
        "event_metadata": {},
        "timestamp": BASE_TIME,
    }
    defaults.update(kw)
    event = AgentEvent(**defaults)
    session.add(event)
    session.flush()
    return event


# --- extract_value ----------------------------------------------------------
@pytest.mark.parametrize(
    "metadata, expected",
    [
        ({"amount": 100}, 100.0),
        ({"amount": 100.5}, 100.5),
        ({"amount": "1,250.75"}, 1250.75),
        ({"transaction_amount": 42}, 42.0),
        ({"value": 7}, 7.0),
        ({"total": 9}, 9.0),
        ({"rows": 48_500}, None),  # not a value key
        ({"amount": "not a number"}, None),
        ({"amount": True}, None),  # bool is not a transaction value
        ({}, None),
        (None, None),
    ],
)
def test_extract_value(metadata, expected):
    assert extract_value(metadata) == expected


def test_value_keys_are_tried_in_priority_order():
    assert extract_value({"total": 9, "amount": 100}) == 100.0


# --- compute_baseline -------------------------------------------------------
@pytest.mark.integration
def test_baseline_of_an_unknown_agent_is_empty(session):
    baseline = compute_baseline(session, "nobody")
    assert baseline.event_count == 0
    assert not baseline.is_established
    assert baseline.value_max is None


@pytest.mark.integration
def test_baseline_accumulates_targets_permissions_and_values(session):
    add_event(session, event_metadata={"amount": 1000})
    add_event(
        session,
        target_id="payments-db",
        target_type="database",
        permissions_used=["db:write_payment"],
        event_metadata={"amount": 1200},
    )
    add_event(session, event_metadata={"amount": 900})
    session.commit()

    baseline = compute_baseline(session, "payment-agent")
    assert baseline.event_count == 3
    assert baseline.known_targets == {"bank-api", "payments-db"}
    assert baseline.known_permissions == {"bank:transfer", "db:write_payment"}
    assert baseline.values == [1000.0, 1200.0, 900.0]
    assert baseline.value_min == 900.0
    assert baseline.value_max == 1200.0


@pytest.mark.integration
def test_blocked_events_never_widen_the_baseline(session):
    """A blocked attempt says what the agent tried, not what it may do."""
    for i in range(3):
        add_event(
            session,
            event_metadata={"amount": 1000},
            timestamp=BASE_TIME + timedelta(minutes=i),
        )
    add_event(
        session,
        target_id="forbidden-api",
        permissions_used=["bank:admin"],
        platform_status="blocked",
        event_metadata={"amount": 500_000},
    )
    session.commit()

    baseline = compute_baseline(session, "payment-agent")
    assert "forbidden-api" not in baseline.known_targets
    assert "bank:admin" not in baseline.known_permissions
    assert baseline.value_max == 1000.0


@pytest.mark.integration
def test_suspicious_events_never_widen_the_baseline(session):
    """Otherwise an agent normalises its own anomalies one at a time."""
    for i in range(3):
        add_event(
            session,
            event_metadata={"amount": 1000},
            timestamp=BASE_TIME + timedelta(minutes=i),
        )
    add_event(
        session,
        target_id="external-agent-x",
        target_type="agent",
        permissions_used=["bank:admin"],
        platform_status="suspicious",
        event_metadata={"amount": 880_000},
    )
    session.commit()

    baseline = compute_baseline(session, "payment-agent")
    assert baseline.known_targets == {"bank-api"}
    assert baseline.known_permissions == {"bank:transfer"}
    assert baseline.value_max == 1000.0


@pytest.mark.integration
def test_baseline_excludes_the_event_under_evaluation(session):
    """An anomalous event must never be allowed to normalise itself."""
    for i in range(3):
        add_event(
            session,
            event_metadata={"amount": 1000},
            timestamp=BASE_TIME + timedelta(minutes=i),
        )
    subject = add_event(
        session,
        target_id="external-agent-x",
        event_metadata={"amount": 880_000},
        timestamp=BASE_TIME + timedelta(minutes=10),
    )
    session.commit()

    baseline = compute_baseline(
        session,
        "payment-agent",
        before=subject.timestamp,
        exclude_event_id=subject.event_id,
    )
    assert "external-agent-x" not in baseline.known_targets
    assert baseline.value_max == 1000.0


@pytest.mark.integration
def test_baseline_only_sees_events_the_agent_itself_initiated(session):
    """Being called by someone is not the same as acting."""
    for i in range(3):
        add_event(session, timestamp=BASE_TIME + timedelta(minutes=i))
    add_event(
        session,
        actor_id="finance-agent",
        target_id="payment-agent",
        target_type="agent",
    )
    session.commit()

    assert compute_baseline(session, "payment-agent").event_count == 3
    assert compute_baseline(session, "finance-agent").event_count == 1


@pytest.mark.integration
def test_established_threshold(session):
    for i in range(MIN_BASELINE_EVENTS - 1):
        add_event(session, timestamp=BASE_TIME + timedelta(minutes=i))
    session.commit()
    assert not compute_baseline(session, "payment-agent").is_established

    add_event(session, timestamp=BASE_TIME + timedelta(minutes=99))
    session.commit()
    assert compute_baseline(session, "payment-agent").is_established
