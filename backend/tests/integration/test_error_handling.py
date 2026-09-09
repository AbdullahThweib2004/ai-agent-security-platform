"""Every endpoint, given bad input.

The bar is: a 4xx with a message a human can act on. Never a 500, never a stack
trace, and never a silent success or a silent empty list — those are how a
mistyped filter becomes a missed alert.
"""

from __future__ import annotations

import pytest

from tests.conftest import event_payload

pytestmark = pytest.mark.integration


def field_errors(response):
    """Map of field path -> message, from a FastAPI validation response."""
    return {
        ".".join(str(p) for p in item["loc"][1:]): item["msg"]
        for item in response.json()["detail"]
    }


def assert_clean_error(response, status):
    """A documented failure: right status, JSON body, human-readable detail."""
    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith("application/json")
    detail = response.json()["detail"]
    assert detail, "an error must say something"
    text = detail if isinstance(detail, str) else str(detail)
    assert "Traceback" not in text
    assert "sqlalchemy" not in text.lower()
    assert "neo4j" not in text.lower() or "graph database" in text.lower()


# --- malformed bodies -------------------------------------------------------
def test_malformed_json_is_422_naming_the_problem(client):
    response = client.post(
        "/events", content=b"{not json", headers={"Content-Type": "application/json"}
    )
    assert_clean_error(response, 422)
    assert response.json()["detail"][0]["type"] == "json_invalid"


def test_empty_body_is_422(client):
    assert_clean_error(client.post("/events", content=b""), 422)


def test_missing_required_fields_names_every_one_of_them(client):
    response = client.post("/events", json={"actor_id": "a"})
    assert_clean_error(response, 422)
    missing = field_errors(response)
    assert set(missing) >= {"actor_type", "target_type", "target_id", "action_type"}
    assert all(msg == "Field required" for msg in missing.values())


@pytest.mark.parametrize(
    "field, value",
    [
        ("actor_type", "robot"),
        ("target_type", "spaceship"),
        ("action_type", "telepathy"),
        ("reported_status", "probably_fine"),
    ],
)
def test_unknown_enum_values_are_rejected_with_the_allowed_set(client, field, value):
    response = client.post("/events", json=event_payload(**{field: value}))
    assert_clean_error(response, 422)
    message = field_errors(response)[field]
    assert "Input should be" in message, message


def test_unknown_action_type_is_rejected_before_the_graph_is_touched(client, neo):
    """Relationship types are interpolated into Cypher, so this must never reach it."""
    response = client.post("/events", json=event_payload(action_type="telepathy"))
    assert_clean_error(response, 422)
    assert "'tool_call'" in field_errors(response)["action_type"]
    assert neo.run("MATCH (n) RETURN count(n) AS c").single()["c"] == 0
    assert client.get("/events").json() == []


@pytest.mark.parametrize("blank", ["", "   ", "\t", "\n  "])
def test_blank_identities_are_rejected(client, blank):
    """A whitespace-only id would become a graph node nobody can name or search."""
    response = client.post("/events", json=event_payload(actor_id=blank))
    assert_clean_error(response, 422)


def test_identities_are_stripped_not_just_validated(client):
    event = client.post(
        "/events", json=event_payload(actor_id="  finance-agent  ")
    ).json()
    assert event["event"]["actor_id"] == "finance-agent"


def test_overlong_identity_is_rejected(client):
    response = client.post("/events", json=event_payload(actor_id="x" * 300))
    assert_clean_error(response, 422)
    assert "at most 255" in field_errors(response)["actor_id"]


@pytest.mark.parametrize(
    "field, value",
    [
        ("permissions_used", "bank:transfer"),
        ("metadata", "nope"),
        ("timestamp", "yesterday"),
    ],
)
def test_wrong_types_are_rejected(client, field, value):
    assert_clean_error(
        client.post("/events", json=event_payload(**{field: value})), 422
    )


@pytest.mark.parametrize("field", ["event_id", "parent_event_id"])
def test_malformed_uuids_in_the_body_are_rejected(client, field):
    response = client.post("/events", json=event_payload(**{field: "not-a-uuid"}))
    assert_clean_error(response, 422)
    assert "UUID" in field_errors(response)[field]


def test_unknown_fields_are_rejected_rather_than_silently_dropped(client):
    """A misspelled `permissions_used` that returns 201 is a lie in an audit log."""
    response = client.post(
        "/events", json={**event_payload(), "permissions_uzed": ["bank:admin"]}
    )
    assert_clean_error(response, 422)
    assert "permissions_uzed" in field_errors(response)
    assert client.get("/events").json() == [], "nothing should have been written"


# --- referential integrity --------------------------------------------------
def test_nonexistent_parent_is_400_not_a_silent_orphan(client):
    response = client.post(
        "/events",
        json=event_payload(parent_event_id="99999999-9999-9999-9999-999999999999"),
    )
    assert_clean_error(response, 400)
    assert "does not exist" in response.json()["detail"]
    assert client.get("/events").json() == [], "no orphan row may be written"


def test_nonexistent_parent_writes_nothing_to_the_graph_either(client, neo):
    client.post(
        "/events",
        json=event_payload(parent_event_id="99999999-9999-9999-9999-999999999999"),
    )
    assert neo.run("MATCH (n) RETURN count(n) AS c").single()["c"] == 0


def test_self_referencing_parent_says_so_plainly(client):
    same = "aaaaaaaa-0000-0000-0000-000000000001"
    response = client.post(
        "/events", json=event_payload(event_id=same, parent_event_id=same)
    )
    assert_clean_error(response, 400)
    assert "cannot be its own parent" in response.json()["detail"]


def test_duplicate_event_id_is_409_with_the_id_in_the_message(client, post_event):
    event_id = post_event()["event"]["event_id"]
    response = client.post("/events", json=event_payload(event_id=event_id))
    assert_clean_error(response, 409)
    assert event_id in response.json()["detail"]


# --- GET routes -------------------------------------------------------------
def test_graph_for_an_unknown_agent_is_404(client, seed_baseline):
    seed_baseline()
    response = client.get("/graph/nobody-here")
    assert_clean_error(response, 404)
    assert "nobody-here" in response.json()["detail"]


def test_graph_for_an_agent_with_no_events_is_404_not_an_empty_200(
    client, seed_baseline
):
    seed_baseline()
    assert client.get("/graph/never-seen").status_code == 404


def test_trailing_slash_does_not_widen_a_scoped_query(client, seed_baseline):
    """GET /graph/ is an empty agent_id — it must not return the whole graph."""
    seed_baseline()
    response = client.get("/graph/")
    assert response.status_code == 404
    assert response.status_code != 200


def test_forensics_for_an_unknown_event_is_404(client):
    response = client.get("/forensics/timeline/99999999-9999-9999-9999-999999999999")
    assert_clean_error(response, 404)
    assert "not found" in response.json()["detail"]


def test_event_detail_for_an_unknown_id_is_404(client):
    assert_clean_error(client.get("/events/99999999-9999-9999-9999-999999999999"), 404)


def test_alert_detail_for_an_unknown_id_is_404(client):
    assert_clean_error(client.get("/alerts/99999999-9999-9999-9999-999999999999"), 404)


@pytest.mark.parametrize(
    "path",
    [
        "/events/not-a-uuid",
        "/alerts/not-a-uuid",
        "/forensics/timeline/not-a-uuid",
    ],
)
def test_malformed_uuids_in_the_path_are_422(client, path):
    assert_clean_error(client.get(path), 422)


# --- query parameters -------------------------------------------------------
@pytest.mark.parametrize(
    "path",
    [
        "/events?limit=0",
        "/events?limit=99999",
        "/events?offset=-5",
        "/events?platform_status=weird",
        "/events?reported_status=weird",
        "/alerts?limit=0",
        "/alerts?limit=99999",
        "/alerts?offset=-1",
        "/graph?limit=0",
        "/graph?limit=99999",
        "/graph/payment-agent?depth=0",
        "/graph/payment-agent?depth=99",
        "/events/reconcile?limit=0",
    ],
)
def test_out_of_range_query_parameters_are_422(client, path):
    method = client.post if "reconcile" in path else client.get
    assert_clean_error(method(path), 422)


@pytest.mark.parametrize("value", ["apocalyptic", "HIGH", "critical"])
def test_unknown_severity_is_422_not_an_empty_list(client, incidentless, value):
    """An empty list is indistinguishable from 'no alerts' — a dangerous answer."""
    response = client.get(f"/alerts?severity={value}")
    assert_clean_error(response, 422)
    assert "Input should be" in response.json()["detail"][0]["msg"]


@pytest.mark.parametrize(
    "value", ["no_such_rule", "unseen counterparty", "UNSEEN_COUNTERPARTY"]
)
def test_unknown_rule_name_is_422_not_an_empty_list(client, incidentless, value):
    assert_clean_error(client.get(f"/alerts?rule_name={value}"), 422)


def test_valid_filters_still_work(client, seed_baseline):
    seed_baseline()
    for path in ("/alerts?severity=high", "/alerts?rule_name=value_excursion"):
        assert client.get(path).status_code == 200


@pytest.fixture
def incidentless(seed_baseline):
    seed_baseline()
    return None


# --- unhandled failures never leak ------------------------------------------
def test_an_unexpected_internal_error_returns_clean_json(client, monkeypatch):
    """A caller must never receive a stack trace."""

    def boom(*args, **kwargs):
        raise RuntimeError("kaboom: secret internal detail")

    monkeypatch.setattr("app.routers.graph.graph_service.get_full_graph", boom)
    response = client.get("/graph", headers={"x-test": "1"})
    assert response.status_code == 500
    body = response.json()
    assert (
        body["detail"] == "An unexpected error occurred. The incident has been logged."
    )
    assert "kaboom" not in str(body)
    assert "secret internal detail" not in str(body)


def test_a_database_outage_is_503_not_500(client, monkeypatch):
    from sqlalchemy.exc import OperationalError

    def unavailable(*args, **kwargs):
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    monkeypatch.setattr("app.routers.graph.graph_service.get_full_graph", unavailable)
    response = client.get("/graph")
    assert response.status_code == 503
    assert "unreachable" in response.json()["detail"]


def test_a_graph_outage_is_503_not_500(client, monkeypatch):
    from neo4j.exceptions import ServiceUnavailable

    def unavailable(*args, **kwargs):
        raise ServiceUnavailable("cannot reach bolt://neo4j:7687")

    monkeypatch.setattr("app.routers.graph.graph_service.get_full_graph", unavailable)
    response = client.get("/graph")
    assert response.status_code == 503
    assert "graph database is unreachable" in response.json()["detail"]
