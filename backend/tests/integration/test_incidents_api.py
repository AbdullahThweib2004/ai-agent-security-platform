"""GET /incidents and GET /incidents/{id}, against real Postgres."""

from __future__ import annotations

from datetime import timedelta

import pytest

from tests.conftest import BASE_TIME

pytestmark = pytest.mark.integration

LATER = BASE_TIME + timedelta(hours=2)


def contain(post_event, actor, target, at):
    """Drive one agent over the threshold."""
    post_event(
        actor_id=actor,
        target_id=target,
        target_type="agent",
        action_type="delegation",
        permissions_used=["bank:transfer", "bank:admin"],
        metadata={"amount": 880_000.0},
        timestamp=at.isoformat(),
    )


@pytest.fixture
def incidents(client, post_event, seed_baseline):
    """Two contained agents, one of them subsequently released."""
    seed_baseline(actor_id="payment-agent", permissions=("bank:transfer",))
    seed_baseline(
        actor_id="finance-agent",
        target_id="fx-rate-tool",
        target_type="tool",
        permissions=("fx:read",),
    )
    contain(post_event, "payment-agent", "external-agent-x", LATER)
    contain(
        post_event, "finance-agent", "another-unknown", LATER + timedelta(minutes=1)
    )

    listed = client.get("/incidents").json()
    resolved = next(i for i in listed if i["agent_id"] == "finance-agent")
    client.post(
        f"/incidents/{resolved['incident_id']}/resolve",
        json={"resolved_by": "sec-oncall", "note": "manipulated, not malicious"},
    )
    return {"contained": "payment-agent", "released": "finance-agent"}


# --- list --------------------------------------------------------------------
def test_no_incidents_when_nothing_crossed_the_threshold(client, seed_baseline):
    seed_baseline()
    assert client.get("/incidents").json() == []


def test_list_returns_every_incident(client, incidents):
    rows = client.get("/incidents").json()
    assert len(rows) == 2
    assert {r["agent_id"] for r in rows} == {"payment-agent", "finance-agent"}
    for row in rows:
        assert row["incident_id"] and row["severity"]
        assert row["trigger_summary"]["layers"]


def test_list_is_newest_first_on_event_time(client, incidents):
    stamps = [r["opened_at"] for r in client.get("/incidents").json()]
    assert stamps == sorted(stamps, reverse=True)


def test_filter_by_agent(client, incidents):
    rows = client.get("/incidents?agent_id=payment-agent").json()
    assert len(rows) == 1
    assert rows[0]["agent_id"] == "payment-agent"


@pytest.mark.parametrize(
    "state, expected", [("open", 1), ("resolved", 1), ("contained", 0)]
)
def test_filter_by_each_status(client, incidents, state, expected):
    rows = client.get(f"/incidents?status={state}").json()
    assert len(rows) == expected
    assert all(r["status"] == state for r in rows)


def test_filters_compose(client, incidents):
    assert len(client.get("/incidents?agent_id=payment-agent&status=open").json()) == 1
    assert client.get("/incidents?agent_id=payment-agent&status=resolved").json() == []


def test_a_valid_filter_matching_nothing_is_an_empty_list(client, incidents):
    """Agent ids are open-ended, so an unknown one is a legitimate empty result."""
    assert client.get("/incidents?agent_id=nobody").json() == []


@pytest.mark.parametrize("value", ["apocalyptic", "OPEN", "closed", "suspended", ""])
def test_an_invalid_status_is_422_not_an_empty_list(client, incidents, value):
    response = client.get(f"/incidents?status={value}")
    assert response.status_code == 422
    assert "Input should be" in response.json()["detail"][0]["msg"]


def test_pagination(client, incidents):
    page = client.get("/incidents?limit=1").json()
    assert len(page) == 1
    rest = client.get("/incidents?limit=1&offset=1").json()
    assert len(rest) == 1
    assert rest[0]["incident_id"] != page[0]["incident_id"]


@pytest.mark.parametrize(
    "path", ["/incidents?limit=0", "/incidents?limit=99999", "/incidents?offset=-1"]
)
def test_out_of_range_paging_is_422(client, path):
    assert client.get(path).status_code == 422


# --- detail ------------------------------------------------------------------
def test_detail_groups_the_evidence_by_layer(client, incidents):
    listed = next(
        i for i in client.get("/incidents").json() if i["agent_id"] == "payment-agent"
    )
    detail = client.get(f"/incidents/{listed['incident_id']}").json()

    grouped = detail["evidence_by_layer"]
    assert grouped, "an incident must show what opened it"
    assert set(grouped) <= {"alert", "delegation", "a2a"}
    for layer, links in grouped.items():
        assert links, f"{layer} group must not be empty"
        for link in links:
            assert link["layer"] == layer
            assert link["event_id"] and link["detail"]


def test_detail_distinguishes_event_time_from_detection_time(client, incidents):
    """The distinction the whole detection design turns on."""
    listed = client.get("/incidents").json()[0]
    detail = client.get(f"/incidents/{listed['incident_id']}").json()

    assert detail["opened_at"] < detail["detected_at"]
    assert detail["detection_lag_seconds"] > 0


def test_detail_matches_the_listed_row(client, incidents):
    listed = client.get("/incidents").json()[0]
    detail = client.get(f"/incidents/{listed['incident_id']}").json()
    assert detail == listed


def test_is_suspended_tracks_status(client, incidents):
    rows = {r["agent_id"]: r for r in client.get("/incidents").json()}
    assert rows["payment-agent"]["is_suspended"] is True
    assert rows["finance-agent"]["is_suspended"] is False
    assert rows["finance-agent"]["resolved_by"] == "sec-oncall"
    assert rows["finance-agent"]["resolution_note"] == "manipulated, not malicious"


def test_unknown_incident_is_404(client):
    response = client.get("/incidents/99999999-9999-9999-9999-999999999999")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"]


def test_malformed_incident_id_is_422(client):
    assert client.get("/incidents/not-a-uuid").status_code == 422


# --- the leak that suspended agents across 50 unrelated tests ----------------
def test_incidents_do_not_leak_between_tests_part_one(
    client, post_event, seed_baseline
):
    """Half of a deliberate pair.

    `incidents` has no foreign key to `agent_events`, so truncating events does
    not cascade to it. When that was missed, one test's open incident suspended
    that agent for every test that followed — 50 unrelated failures with no
    obvious cause. These two tests fail loudly if the fixture ever regresses.
    """
    seed_baseline(actor_id="payment-agent", permissions=("bank:transfer",))
    contain(post_event, "payment-agent", "external-agent-x", LATER)
    assert len(client.get("/incidents").json()) == 1


def test_incidents_do_not_leak_between_tests_part_two(client, session):
    """The other half: this must see none of the above."""
    from app.models.incident import Incident, IncidentEvent
    from app.services.incidents import is_suspended

    assert client.get("/incidents").json() == []
    assert session.query(Incident).count() == 0
    assert session.query(IncidentEvent).count() == 0
    assert is_suspended(session, "payment-agent") is False
