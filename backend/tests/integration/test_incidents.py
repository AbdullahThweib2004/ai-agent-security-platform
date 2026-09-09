"""Containment end to end: detection, suspension, and operator release."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.models.event import AgentEvent
from app.models.incident import Incident
from app.services.identity import trust_level_of
from app.services.incidents import is_suspended
from tests.conftest import BASE_TIME

pytestmark = pytest.mark.integration

LATER = BASE_TIME + timedelta(hours=2)


@pytest.fixture
def contained_agent(client, post_event, seed_baseline):
    """Drive payment-agent over the threshold, the way the real scenario does."""
    seed_baseline(actor_id="payment-agent", permissions=("bank:transfer",))
    # A high-value handoff to an agent nobody has seen: three alerts at once,
    # two of them high severity.
    post_event(
        actor_id="payment-agent",
        target_id="external-agent-x",
        target_type="agent",
        action_type="delegation",
        permissions_used=["bank:transfer", "bank:admin"],
        metadata={"amount": 880_000.0},
        timestamp=LATER.isoformat(),
    )
    return "payment-agent"


def incident_of(session, agent_id):
    return session.query(Incident).filter_by(agent_id=agent_id).one_or_none()


# --- detection ---------------------------------------------------------------
def test_crossing_the_threshold_opens_an_incident(client, session, contained_agent):
    incident = incident_of(session, contained_agent)
    assert incident is not None
    assert incident.status == "open"
    assert incident.severity == "high"
    assert incident.trigger_summary["signal_count"] >= 2
    assert set(incident.trigger_summary["layers"]) >= {"alert"}


def test_the_incident_is_dated_to_event_time_not_detection_time(
    client, session, contained_agent
):
    """The distinction the whole design turns on."""
    incident = incident_of(session, contained_agent)
    assert incident.opened_at == LATER
    assert incident.detected_at > incident.opened_at


def test_the_evidence_records_which_layers_fired(client, session, contained_agent):
    incident = incident_of(session, contained_agent)
    layers = {e.layer for e in incident.evidence}
    assert "alert" in layers
    for link in incident.evidence:
        assert link.detail, "every evidence row explains itself"


def test_ordinary_behaviour_does_not_open_an_incident(client, session, seed_baseline):
    seed_baseline(actor_id="payment-agent", permissions=("bank:transfer",))
    assert incident_of(session, "payment-agent") is None


def test_a_cold_start_agent_is_never_contained(client, post_event, session):
    """Containment must not fire on the platform's own ignorance.

    A brand-new agent's first contacts trip other layers precisely because
    nothing is known about it yet — that is absence of evidence, not evidence.
    """
    post_event(
        actor_id="brand-new-agent",
        target_id="another-new-agent",
        target_type="agent",
        action_type="delegation",
        permissions_used=["bank:admin"],
        metadata={"amount": 999_999.0},
        timestamp=LATER.isoformat(),
    )
    assert incident_of(session, "brand-new-agent") is None


def test_an_agent_already_contained_does_not_open_a_second_incident(
    client, post_event, session, contained_agent
):
    post_event(
        actor_id="payment-agent",
        target_id="offshore-api",
        target_type="api",
        action_type="api_call",
        permissions_used=["net:egress"],
        metadata={"amount": 500_000.0},
        timestamp=(LATER + timedelta(minutes=5)).isoformat(),
    )
    assert session.query(Incident).filter_by(agent_id="payment-agent").count() == 1


# --- suspension --------------------------------------------------------------
def test_containment_suspends_the_agent(client, session, contained_agent):
    assert is_suspended(session, contained_agent) is True


def test_a_suspended_agent_resolves_as_untrusted(client, session, contained_agent):
    """The override reaches every layer through the one function they all ask."""
    assert trust_level_of(session, contained_agent) == "external_untrusted"


def test_suspension_overrides_an_operator_assertion(client, session, contained_agent):
    """Containment outranks a prior vouching — that is what makes it containment."""
    client.post(f"/agents/{contained_agent}/trust", json={"trust_level": "internal"})
    session.expire_all()
    assert trust_level_of(session, contained_agent) == "external_untrusted"


def test_a_suspended_agents_interactions_are_blocked(
    client, post_event, session, contained_agent
):
    from app.models.a2a import A2ADecision

    body = post_event(
        actor_id=contained_agent,
        target_id="finance-agent",
        target_type="agent",
        action_type="agent_message",
        timestamp=(LATER + timedelta(minutes=10)).isoformat(),
    )
    decision = (
        session.query(A2ADecision).filter_by(event_id=body["event"]["event_id"]).one()
    )
    assert decision.decision == "blocked"
    assert decision.rule == "untrusted_party"


def test_nothing_may_be_delegated_to_a_suspended_agent(
    client, post_event, session, contained_agent
):
    """A suspended agent has plenty of history, so the rating rule alone would
    have let it through — containment refuses regardless."""
    from app.models.delegation import Delegation

    for i in range(4):
        post_event(
            actor_id="analyst-1",
            actor_type="user",
            target_id="ledger-tool",
            target_type="tool",
            action_type="tool_call",
            permissions_used=["fx:read"],
            timestamp=(BASE_TIME + timedelta(minutes=i)).isoformat(),
        )
    body = post_event(
        actor_id="analyst-1",
        actor_type="user",
        target_id=contained_agent,
        target_type="agent",
        action_type="delegation",
        permissions_used=[],
        metadata={"requested_permissions": ["fx:read"]},
        timestamp=(LATER + timedelta(minutes=20)).isoformat(),
    )
    delegation = (
        session.query(Delegation).filter_by(event_id=body["event"]["event_id"]).one()
    )
    assert delegation.decision == "blocked"
    assert delegation.permission_decisions[0]["rule"] == "untrusted_delegate"


# --- record and mark ---------------------------------------------------------
def test_events_from_a_contained_agent_are_still_recorded(
    client, post_event, session, contained_agent
):
    """Containment must not create a blind spot.

    A suspended agent's actions are exactly the ones an investigator most wants.
    """
    before = session.query(AgentEvent).filter_by(actor_id=contained_agent).count()
    body = post_event(
        actor_id=contained_agent,
        target_id="bank-api",
        target_type="api",
        action_type="api_call",
        permissions_used=["bank:transfer"],
        metadata={"amount": 10.0},
        timestamp=(LATER + timedelta(minutes=30)).isoformat(),
    )
    after = session.query(AgentEvent).filter_by(actor_id=contained_agent).count()

    assert after == before + 1, "the event must be recorded, not dropped"
    assert body["event"]["actor_suspended"] is True


def test_the_marker_is_recorded_not_joined(
    client, post_event, session, contained_agent
):
    """Resolving the incident must not rewrite the past.

    A join against current incident status would make every event ingested
    during the suspension quietly stop looking suspended.
    """
    body = post_event(
        actor_id=contained_agent,
        target_id="bank-api",
        target_type="api",
        action_type="api_call",
        permissions_used=["bank:transfer"],
        timestamp=(LATER + timedelta(minutes=40)).isoformat(),
    )
    incident = incident_of(session, contained_agent)
    client.post(
        f"/incidents/{incident.incident_id}/resolve", json={"resolved_by": "sec-oncall"}
    )

    session.expire_all()
    event = session.get(AgentEvent, body["event"]["event_id"])
    assert event.actor_suspended is True, "the marker must survive the release"


def test_contained_activity_does_not_widen_the_baseline(
    client, post_event, session, contained_agent
):
    """An agent must not emerge from containment better-rated than it went in."""
    from app.services.baseline import compute_baseline

    before = compute_baseline(session, contained_agent).event_count
    for i in range(5):
        post_event(
            actor_id=contained_agent,
            target_id="bank-api",
            target_type="api",
            action_type="api_call",
            permissions_used=["bank:transfer"],
            timestamp=(LATER + timedelta(hours=1, minutes=i)).isoformat(),
        )
    session.expire_all()
    assert compute_baseline(session, contained_agent).event_count == before


# --- operator release --------------------------------------------------------
def test_an_operator_can_release_a_contained_agent(client, session, contained_agent):
    incident = incident_of(session, contained_agent)
    body = client.post(
        f"/incidents/{incident.incident_id}/resolve",
        json={"resolved_by": "sec-oncall", "note": "confirmed false positive"},
    ).json()

    assert body["status"] == "resolved"
    assert body["resolved_by"] == "sec-oncall"
    assert body["resolution_note"] == "confirmed false positive"
    assert body["resolved_at"] is not None
    assert body["is_suspended"] is False

    session.expire_all()
    assert is_suspended(session, contained_agent) is False
    assert trust_level_of(session, contained_agent) == "unrated"


def test_resolving_twice_is_rejected_rather_than_a_silent_no_op(
    client, session, contained_agent
):
    incident = incident_of(session, contained_agent)
    first = client.post(
        f"/incidents/{incident.incident_id}/resolve", json={"resolved_by": "alice"}
    )
    assert first.status_code == 200

    second = client.post(
        f"/incidents/{incident.incident_id}/resolve", json={"resolved_by": "bob"}
    )
    assert second.status_code == 400
    assert "already resolved" in second.json()["detail"]
    assert "alice" in second.json()["detail"], "say who resolved it, and when"


def test_release_requires_a_named_operator(client, session, contained_agent):
    incident = incident_of(session, contained_agent)
    for body in ({}, {"resolved_by": ""}, {"note": "no operator"}):
        assert (
            client.post(
                f"/incidents/{incident.incident_id}/resolve", json=body
            ).status_code
            == 422
        )


def test_after_release_a_new_incident_can_open(
    client, post_event, session, contained_agent
):
    incident = incident_of(session, contained_agent)
    client.post(
        f"/incidents/{incident.incident_id}/resolve", json={"resolved_by": "sec-oncall"}
    )
    post_event(
        actor_id=contained_agent,
        target_id="another-unknown-agent",
        target_type="agent",
        action_type="delegation",
        permissions_used=["bank:transfer", "bank:admin"],
        metadata={"amount": 990_000.0},
        timestamp=(LATER + timedelta(hours=2)).isoformat(),
    )
    session.expire_all()
    assert session.query(Incident).filter_by(agent_id=contained_agent).count() == 2


def test_resolving_an_unknown_incident_is_404(client):
    response = client.post(
        "/incidents/99999999-9999-9999-9999-999999999999/resolve",
        json={"resolved_by": "sec-oncall"},
    )
    assert response.status_code == 404


def test_a_malformed_incident_id_is_422(client):
    assert (
        client.post(
            "/incidents/not-a-uuid/resolve", json={"resolved_by": "x"}
        ).status_code
        == 422
    )


# --- guards that only matter in isolation ------------------------------------
def test_upstream_citations_alone_never_corroborate(client, post_event, session):
    """Two relayed conclusions are not two independent layers.

    A rated agent reaching two counterparties it already knows — but which are
    themselves still unrated — gets an interaction block per event, and a
    delegation that merely *cites* it. Counting the citation would make one
    finding look like two layers agreeing across two events, and contain a
    healthy agent for reaching someone the platform simply cannot vouch for.

    The first contacts happen before the actor is rated, so the anomaly layer
    never judges them; that keeps alerts out of the picture and leaves the
    upstream-citation guard as the only thing standing between this agent and
    containment.
    """
    # 1-2: first contacts, while the actor is still unrated (no alerts fire).
    for i, target in enumerate(("newcomer-one", "newcomer-two")):
        post_event(
            actor_id="busy-agent",
            target_id=target,
            target_type="agent",
            action_type="agent_message",
            permissions_used=["fx:read"],
            timestamp=(LATER + timedelta(minutes=i)).isoformat(),
        )
    # 3-5: enough clean history to be rated.
    for i in range(3):
        post_event(
            actor_id="busy-agent",
            target_id="fx-rate-tool",
            target_type="tool",
            action_type="tool_call",
            permissions_used=["fx:read"],
            timestamp=(LATER + timedelta(minutes=10 + i)).isoformat(),
        )
    # 6-7: delegations to those same, now-familiar but still unrated agents.
    for i, target in enumerate(("newcomer-one", "newcomer-two")):
        body = post_event(
            actor_id="busy-agent",
            target_id=target,
            target_type="agent",
            action_type="delegation",
            permissions_used=[],
            metadata={"requested_permissions": ["fx:read"]},
            timestamp=(LATER + timedelta(minutes=20 + i)).isoformat(),
        )
        assert body["alerts"] == [], "the anomaly layer must stay out of this test"

    from app.models.a2a import A2ADecision
    from app.models.delegation import Delegation

    blocked_delegations = session.query(Delegation).filter_by(decision="blocked").all()
    assert len(blocked_delegations) == 2
    for delegation in blocked_delegations:
        assert all(
            v["rule"] == "upstream_a2a_block" for v in delegation.permission_decisions
        ), "each delegation must be a pure citation, adding no reasoning of its own"
    assert session.query(A2ADecision).filter_by(decision="blocked").count() >= 2

    assert incident_of(session, "busy-agent") is None


def test_a_cold_start_agent_is_spared_even_with_two_layers_across_two_events(
    client, post_event, session
):
    """The cold-start guard, isolated from the distinct-event requirement.

    Here the shape *does* satisfy corroboration — two layers, two events, each
    delegation refused by confinement on its own reasoning — but the actor has
    almost no history, so every one of those signals describes what the platform
    does not know rather than what the agent did.
    """
    for i, target in enumerate(("newcomer-one", "newcomer-two")):
        post_event(
            actor_id="fresh-agent",
            target_id=target,
            target_type="agent",
            action_type="delegation",
            permissions_used=[],
            metadata={"requested_permissions": ["bank:admin"]},
            timestamp=(LATER + timedelta(minutes=i)).isoformat(),
        )

    from app.models.delegation import Delegation
    from app.services.baseline import compute_baseline
    from app.services.trust import is_rated

    # Precondition: the delegations reasoned independently (confinement), so the
    # upstream-citation guard is not what is doing the work here.
    rules = {
        v["rule"]
        for d in session.query(Delegation).filter_by(decision="blocked").all()
        for v in d.permission_decisions
    }
    assert "confinement" in rules
    assert not is_rated(compute_baseline(session, "fresh-agent").event_count)

    assert incident_of(session, "fresh-agent") is None
