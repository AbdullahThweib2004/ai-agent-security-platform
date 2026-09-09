"""GET /alerts, its filters, and the single-alert route."""

from __future__ import annotations

from datetime import timedelta

import pytest

from tests.conftest import BASE_TIME

pytestmark = pytest.mark.integration


@pytest.fixture
def incident(post_event, seed_baseline):
    """Two flagged events by two different agents, six alerts in total."""
    seed_baseline(actor_id="payment-agent")
    seed_baseline(
        actor_id="finance-agent",
        target_id="payment-agent",
        target_type="agent",
        permissions=("payments:initiate",),
    )

    payment = post_event(
        actor_id="payment-agent",
        target_id="external-agent-x",
        target_type="agent",
        action_type="delegation",
        permissions_used=["bank:transfer", "bank:admin"],
        metadata={"amount": 880_000.0},
        timestamp=(BASE_TIME + timedelta(hours=1)).isoformat(),
    )
    finance = post_event(
        actor_id="finance-agent",
        target_id="payment-agent",
        target_type="agent",
        action_type="delegation",
        permissions_used=["payments:initiate"],
        metadata={"amount": 880_000.0},
        timestamp=(BASE_TIME + timedelta(hours=1, minutes=1)).isoformat(),
    )
    return {"payment": payment, "finance": finance}


def test_no_alerts_when_nothing_fired(client, seed_baseline):
    seed_baseline()
    assert client.get("/alerts").json() == []


def test_alerts_carry_the_triggering_event_inline(client, incident):
    alerts = client.get("/alerts").json()
    assert len(alerts) == 4
    for alert in alerts:
        assert alert["event"]["event_id"]
        assert alert["event"]["actor_id"]
        assert alert["details"]["reason"]
        assert alert["severity"] in {"high", "medium", "low"}


def test_alerts_are_newest_first(client, incident):
    stamps = [a["triggered_at"] for a in client.get("/alerts").json()]
    assert stamps == sorted(stamps, reverse=True)


def test_filter_by_rule_name(client, incident):
    unseen = client.get("/alerts?rule_name=unseen_counterparty").json()
    assert len(unseen) == 1
    assert unseen[0]["event"]["target_id"] == "external-agent-x"
    assert client.get("/alerts?rule_name=value_excursion").json()


def test_filter_by_severity(client, incident):
    high = client.get("/alerts?severity=high").json()
    assert high
    assert {a["severity"] for a in high} == {"high"}


def test_filter_by_agent(client, incident):
    """agent_id filters on the actor that triggered the rule, not the target."""
    payment = client.get("/alerts?agent_id=payment-agent").json()
    assert payment
    assert {a["event"]["actor_id"] for a in payment} == {"payment-agent"}

    finance = client.get("/alerts?agent_id=finance-agent").json()
    assert {a["event"]["actor_id"] for a in finance} == {"finance-agent"}


def test_filters_compose(client, incident):
    combined = client.get(
        "/alerts?agent_id=payment-agent&rule_name=new_permission&severity=medium"
    ).json()
    assert len(combined) == 1
    assert combined[0]["rule_name"] == "new_permission"


def test_a_valid_filter_matching_nothing_returns_an_empty_list(client, incident):
    """An empty result is only acceptable when the filter itself was valid."""
    # agent ids are open-ended, so an unknown one is a legitimate empty result
    assert client.get("/alerts?agent_id=nobody").json() == []
    # a rule that exists but did not fire here
    assert (
        client.get("/alerts?rule_name=new_permission&agent_id=finance-agent").json()
        == []
    )


def test_an_invalid_filter_value_is_rejected_rather_than_returning_nothing(
    client, incident
):
    """A mistyped rule name must not look like 'no alerts' — that hides findings."""
    assert client.get("/alerts?rule_name=no_such_rule").status_code == 422
    assert client.get("/alerts?severity=apocalyptic").status_code == 422


def test_pagination(client, incident):
    page = client.get("/alerts?limit=2").json()
    assert len(page) == 2
    assert (
        client.get("/alerts?limit=2&offset=2").json()[0]["alert_id"]
        != page[0]["alert_id"]
    )


def test_single_alert_detail(client, incident):
    listed = client.get("/alerts").json()[0]
    fetched = client.get(f"/alerts/{listed['alert_id']}").json()
    assert fetched["alert_id"] == listed["alert_id"]
    assert fetched["details"] == listed["details"]


def test_unknown_alert_is_404(client):
    response = client.get("/alerts/99999999-9999-9999-9999-999999999999")
    assert response.status_code == 404


def test_malformed_alert_id_is_422(client):
    assert client.get("/alerts/not-a-uuid").status_code == 422


def test_evidence_is_frozen_at_trigger_time(
    client, post_event, seed_baseline, incident
):
    """A later shift in the baseline must not rewrite what an alert said."""
    before = client.get("/alerts?rule_name=value_excursion").json()[0]
    captured_max = before["details"]["baseline_max"]

    for i in range(4):
        post_event(
            actor_id="payment-agent",
            metadata={"amount": 5000.0},
            timestamp=(BASE_TIME + timedelta(hours=2, minutes=i)).isoformat(),
        )

    after = client.get(f"/alerts/{before['alert_id']}").json()
    assert after["details"]["baseline_max"] == captured_max
