"""POST /agents/{agent_id}/trust — operator-asserted trust levels."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app.models.identity import AgentIdentity
from tests.conftest import BASE_TIME

pytestmark = pytest.mark.integration

ASSERTABLE = ["internal", "external_trusted", "external_untrusted"]


@pytest.mark.parametrize("level", ASSERTABLE)
def test_an_operator_can_assert_each_level(client, level):
    response = client.post("/agents/partner-agent/trust", json={"trust_level": level})
    assert response.status_code == 200
    body = response.json()
    assert body["agent_id"] == "partner-agent"
    assert body["trust_level"] == level


def test_asserting_creates_the_identity_when_none_exists(client, session):
    """Pre-marking a known-bad counterparty must not require a prior event.

    Otherwise a compromised partner gets one free interaction before anyone can
    act on it.
    """
    assert session.get(AgentIdentity, "never-seen-agent") is None

    body = client.post(
        "/agents/never-seen-agent/trust", json={"trust_level": "external_untrusted"}
    ).json()

    assert body["trust_level"] == "external_untrusted"
    assert body["is_known"] is False
    assert body["first_seen"] is None
    assert body["last_seen"] is None


def test_asserting_twice_updates_rather_than_duplicating(client, session):
    client.post("/agents/partner-agent/trust", json={"trust_level": "external_trusted"})
    body = client.post(
        "/agents/partner-agent/trust", json={"trust_level": "external_untrusted"}
    ).json()

    assert body["trust_level"] == "external_untrusted"
    rows = session.query(AgentIdentity).filter_by(agent_id="partner-agent").all()
    assert len(rows) == 1


def test_a_trust_assertion_does_not_touch_last_seen(client, post_event, session):
    """last_seen tracks observed activity; an administrative act is not activity."""
    post_event(actor_id="finance-agent", timestamp=BASE_TIME.isoformat())
    session.expire_all()
    before = session.get(AgentIdentity, "finance-agent")
    assert before is not None, "ingest should have created the identity"
    first_seen, last_seen = before.first_seen, before.last_seen

    client.post("/agents/finance-agent/trust", json={"trust_level": "internal"})

    session.expire_all()
    after = session.get(AgentIdentity, "finance-agent")
    assert after.trust_level == "internal"
    assert after.first_seen == first_seen
    assert after.last_seen == last_seen


def test_activity_after_an_assertion_moves_last_seen_but_keeps_the_level(
    client, post_event, session
):
    client.post("/agents/finance-agent/trust", json={"trust_level": "internal"})
    post_event(actor_id="finance-agent", timestamp=BASE_TIME.isoformat())
    post_event(
        actor_id="finance-agent",
        timestamp=(BASE_TIME + timedelta(hours=3)).isoformat(),
    )

    session.expire_all()
    identity = session.get(AgentIdentity, "finance-agent")
    assert identity.trust_level == "internal", "activity must not clear an assertion"
    assert identity.last_seen > identity.first_seen
    assert identity.first_seen is not None


# --- validation -------------------------------------------------------------
@pytest.mark.parametrize(
    "level", ["unrated", "trusted", "INTERNAL", "", "internal ", "superuser"]
)
def test_an_invalid_trust_level_is_422(client, level):
    """`unrated` included deliberately: it is derived, not assertable."""
    response = client.post("/agents/some-agent/trust", json={"trust_level": level})
    assert response.status_code == 422
    assert "Input should be" in response.json()["detail"][0]["msg"]


def test_a_missing_body_is_422(client):
    assert client.post("/agents/some-agent/trust", json={}).status_code == 422


def test_an_unknown_field_is_rejected(client):
    response = client.post(
        "/agents/some-agent/trust",
        json={"trust_level": "internal", "trust_levl": "external_trusted"},
    )
    assert response.status_code == 422


def test_a_blank_agent_id_is_422(client):
    assert (
        client.post("/agents/%20/trust", json={"trust_level": "internal"}).status_code
        == 422
    )


# --- reading ----------------------------------------------------------------
def test_reading_an_unknown_identity_is_404(client):
    response = client.get("/agents/nobody/trust")
    assert response.status_code == 404
    assert "no identity on record" in response.json()["detail"]


def test_an_agent_seen_but_never_asserted_defaults_to_unrated(client, post_event):
    post_event(actor_id="finance-agent", timestamp=BASE_TIME.isoformat())
    body = client.get("/agents/finance-agent/trust").json()
    assert body["trust_level"] == "unrated"
    assert body["is_known"] is True
    assert body["first_seen"] is not None


def test_is_known_is_derived_from_first_seen(client, post_event):
    client.post("/agents/ghost-agent/trust", json={"trust_level": "internal"})
    assert client.get("/agents/ghost-agent/trust").json()["is_known"] is False

    post_event(actor_id="ghost-agent", timestamp=BASE_TIME.isoformat())
    body = client.get("/agents/ghost-agent/trust").json()
    assert body["is_known"] is True
    assert body["trust_level"] == "internal"


# --- the derived default, as consumed by policy -----------------------------
def test_trust_level_of_an_unknown_agent_is_unrated(session):
    """The safe default: an agent nobody classified is never trusted by accident."""
    from app.services.identity import trust_level_of

    assert trust_level_of(session, "no-such-agent") == "unrated"


def test_trust_level_of_a_seen_but_unasserted_agent_is_unrated(
    client, post_event, session
):
    from app.services.identity import trust_level_of

    post_event(actor_id="finance-agent", timestamp=BASE_TIME.isoformat())
    session.expire_all()
    assert trust_level_of(session, "finance-agent") == "unrated"


def test_trust_level_of_returns_the_assertion_once_made(client, session):
    from app.services.identity import trust_level_of

    client.post("/agents/partner-agent/trust", json={"trust_level": "external_trusted"})
    session.expire_all()
    assert trust_level_of(session, "partner-agent") == "external_trusted"


def test_first_seen_moves_backwards_for_a_backfilled_event(client, post_event, session):
    """first_seen is the earliest observation, not the first one ingested."""
    post_event(actor_id="finance-agent", timestamp=BASE_TIME.isoformat())
    post_event(
        actor_id="finance-agent",
        timestamp=(BASE_TIME - timedelta(days=5)).isoformat(),
    )
    session.expire_all()
    identity = session.get(AgentIdentity, "finance-agent")
    assert identity.first_seen < BASE_TIME
    assert identity.last_seen == BASE_TIME
