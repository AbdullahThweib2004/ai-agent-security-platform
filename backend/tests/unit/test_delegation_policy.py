"""The delegation policy engine, rule by rule.

No database: a policy decision is a pure function of (requested permission,
delegator baseline, delegate baseline), and testing it that way pins each
boundary precisely.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.baseline import AgentBaseline
from app.services.delegation_policy import (
    PermissionVerdict,
    _summarise,
    categorise,
    decide_permission,
    evaluate_delegation,
    extract_requested_permissions,
    reduced_form,
)
from app.services.trust import MIN_BASELINE_EVENTS

ALLOWED, LIMITED, BLOCKED = "allowed", "limited", "blocked"


def agent(agent_id="payment-agent", permissions=(), events=10) -> AgentBaseline:
    return AgentBaseline(
        agent_id=agent_id,
        event_count=events,
        known_permissions=set(permissions),
    )


def rated(agent_id="delegate-agent", permissions=()) -> AgentBaseline:
    """A delegate with enough history to be judged."""
    return agent(agent_id, permissions, events=MIN_BASELINE_EVENTS + 5)


def unrated(agent_id="external-agent-x") -> AgentBaseline:
    return agent(agent_id, (), events=MIN_BASELINE_EVENTS - 1)


def make_event(permissions_used=(), metadata=None):
    return SimpleNamespace(
        actor_id="payment-agent",
        target_id="delegate-agent",
        permissions_used=list(permissions_used),
        event_metadata=metadata or {},
    )


# --- requested-permission extraction ----------------------------------------
def test_explicit_requested_permissions_win():
    event = make_event(
        permissions_used=["bank:transfer"],
        metadata={"requested_permissions": ["fx:read", "ledger:reconcile"]},
    )
    assert extract_requested_permissions(event) == ["fx:read", "ledger:reconcile"]


def test_falls_back_to_permissions_used_when_not_declared():
    """Every emitter predating this feature is governed without a backfill."""
    event = make_event(permissions_used=["bank:transfer", "bank:admin"])
    assert extract_requested_permissions(event) == ["bank:transfer", "bank:admin"]


@pytest.mark.parametrize("bad", ["not-a-list", 42, {"a": 1}, None])
def test_malformed_requested_permissions_fall_back(bad):
    event = make_event(
        permissions_used=["fx:read"], metadata={"requested_permissions": bad}
    )
    assert extract_requested_permissions(event) == ["fx:read"]


def test_requested_permissions_are_deduped_and_cleaned():
    event = make_event(
        metadata={"requested_permissions": ["a:b", " a:b ", "", "c:d", 7]}
    )
    assert extract_requested_permissions(event) == ["a:b", "c:d"]


def test_no_permissions_requested_is_an_empty_list():
    assert extract_requested_permissions(make_event()) == []


# --- categorisation ---------------------------------------------------------
@pytest.mark.parametrize(
    "permission, category",
    [
        ("db:read_pii", "customer_data"),
        ("customer.read", "customer_data"),
        ("data:kyc_lookup", "customer_data"),
        ("bank:admin", "administrative"),
        ("iam:grant", "administrative"),
        ("bank:transfer", "money_movement"),
        ("payments:wire", "money_movement"),
        ("net:egress", "egress"),
        ("fx:read", None),
        ("ledger:reconcile", None),
        ("db:read_payment", None),
    ],
)
def test_categorise(permission, category):
    assert categorise(permission) == category


@pytest.mark.parametrize(
    "permission",
    [
        "geo:administrative_region",
        "report:transferable_summary",
        "billing:settlements_view",
    ],
)
def test_a_keyword_must_occupy_a_whole_segment(permission):
    """Prefix matching classified "transferable" as money movement.

    Over-blocking is not harmless: a decision an analyst can see is wrong is one
    they stop trusting.
    """
    assert categorise(permission) is None


# --- reduced forms ----------------------------------------------------------
@pytest.mark.parametrize(
    "permission, reduced",
    [
        ("bank:transfer", "bank:read"),
        ("db:write_payment", "db:read_payment"),
        ("db:read_pii", "db:read_masked"),
        ("orders:create", "orders:read"),
        ("records:delete", "records:read"),
        ("payments:initiate", "payments:read"),
        ("fx:read", None),
    ],
)
def test_reduced_form(permission, reduced):
    assert reduced_form(permission) == reduced


# --- rule 1: confinement ----------------------------------------------------
def test_delegator_cannot_pass_on_what_it_does_not_hold():
    verdict = decide_permission("bank:admin", agent(permissions=["fx:read"]), rated())
    assert verdict.decision == BLOCKED
    assert verdict.rule == "confinement"
    assert verdict.granted_as is None
    assert "never held" in verdict.reason


def test_confinement_outranks_everything_else():
    """Even for a benign permission and a well-rated delegate."""
    verdict = decide_permission("fx:read", agent(permissions=[]), rated())
    assert (verdict.decision, verdict.rule) == (BLOCKED, "confinement")


def test_a_held_unrestricted_permission_is_allowed_as_requested():
    verdict = decide_permission("fx:read", agent(permissions=["fx:read"]), rated())
    assert verdict.decision == ALLOWED
    assert verdict.granted_as == "fx:read"
    assert verdict.rule == "default_allow"


# --- rule 2: sensitive categories -------------------------------------------
def test_sensitive_permission_is_reduced_not_passed_through():
    delegator = agent(permissions=["bank:transfer", "bank:read"])
    verdict = decide_permission("bank:transfer", delegator, rated())
    assert verdict.decision == LIMITED
    assert verdict.rule == "sensitive_category"
    assert verdict.granted_as == "bank:read"
    assert verdict.granted_as != verdict.permission


def test_sensitive_permission_is_blocked_when_the_delegator_lacks_the_reduced_form():
    """The downgrade must not manufacture authority the delegator never had."""
    delegator = agent(permissions=["bank:transfer"])  # no bank:read
    verdict = decide_permission("bank:transfer", delegator, rated())
    assert verdict.decision == BLOCKED
    assert verdict.rule == "sensitive_category"
    assert "does not hold that either" in verdict.reason


@pytest.mark.parametrize("permission", ["bank:admin", "net:egress", "iam:grant"])
def test_categories_with_no_safe_reduction_are_blocked_outright(permission):
    delegator = agent(permissions=[permission, "bank:read"])
    verdict = decide_permission(permission, delegator, rated())
    assert verdict.decision == BLOCKED
    assert verdict.rule == "sensitive_category"
    assert "no reduced form" in verdict.reason


def test_no_safe_reduction_beats_an_available_reduced_form():
    """Regression: a surviving mutation.

    Disabling the NO_SAFE_REDUCTION guard changed nothing for today's permission
    names, because no admin or egress permission happens to have a derivable
    read form — so the whole guard could be deleted and every test still passed.
    ``admin:create_user`` is the case that separates them: it is administrative
    *and* reduces to ``admin:read_user``. Administrative authority has no
    read-only version that is safe to hand on, so it must still be refused
    outright rather than quietly downgraded.
    """
    delegator = agent(permissions=["admin:create_user", "admin:read_user"])
    verdict = decide_permission("admin:create_user", delegator, rated())

    assert reduced_form("admin:create_user") == "admin:read_user"
    assert verdict.decision == BLOCKED
    assert verdict.granted_as is None
    assert verdict.rule == "sensitive_category"
    assert "administrative authority" in verdict.reason


def test_a_sensitive_permission_with_no_reduced_form_is_blocked():
    """money_movement and customer_data are reducible categories in principle.

    An individual permission in them may still have no read-only form to fall
    back to — ``payments:wire`` names an action, not a resource — and there is
    nothing safe to grant instead.
    """
    delegator = agent(permissions=["payments:wire", "payments:read"])
    verdict = decide_permission("payments:wire", delegator, rated())

    assert categorise("payments:wire") == "money_movement"
    assert reduced_form("payments:wire") is None
    assert verdict.decision == BLOCKED
    assert verdict.rule == "sensitive_category"
    assert verdict.granted_as is None
    assert "no reduced form to grant instead" in verdict.reason


def test_egress_is_refused_even_when_a_reduced_form_is_held():
    delegator = agent(permissions=["egress:upload_file", "egress:read_file"])
    verdict = decide_permission("egress:upload_file", delegator, rated())
    assert verdict.decision == BLOCKED
    assert verdict.granted_as is None


def test_an_explicitly_categorised_permission_is_reduced_not_passed_on():
    """The case the seeded demo depends on: db:write_payment -> db:read_payment.

    No keyword in the name says "money movement", so it is classified by exact
    name. Widening a keyword to reach it would be the wrong tool — adding
    "payment" to the money_movement family would also capture
    `finance:request_payment`, which has no reduced form and is delegated on the
    ordinary invoice path, so the policy would block the primary workflow.
    """
    delegator = agent(permissions=["db:write_payment", "db:read_payment"])
    verdict = decide_permission("db:write_payment", delegator, rated())

    assert categorise("db:write_payment") == "money_movement"
    assert verdict.decision == LIMITED
    assert verdict.rule == "sensitive_category"
    assert verdict.granted_as == "db:read_payment"


def test_the_reduced_form_is_not_itself_treated_as_unsafe():
    """A reduction the policy grants cannot be one it considers unsafe to grant.

    If db:read_payment were also classified sensitive, the substitution above
    would be incoherent — handing over something the policy would itself refuse.
    """
    assert categorise("db:read_payment") is None


@pytest.mark.parametrize("permission", ["finance:request_payment", "payments:initiate"])
def test_the_ordinary_invoice_permissions_stay_unclassified(permission):
    """Regression guard on blast radius.

    These two are delegated on every invoice run. Classifying either as
    sensitive would block the workflow the platform exists to observe.
    """
    assert categorise(permission) is None


def test_customer_data_is_never_auto_granted_even_when_held():
    delegator = agent(permissions=["db:read_pii", "db:read_masked"])
    verdict = decide_permission("db:read_pii", delegator, rated())
    assert verdict.decision == LIMITED
    assert verdict.granted_as == "db:read_masked"


# --- rule 3: unrated delegate -----------------------------------------------
def test_unrated_delegate_receives_nothing_even_for_a_benign_permission():
    verdict = decide_permission("fx:read", agent(permissions=["fx:read"]), unrated())
    assert verdict.decision == BLOCKED
    assert verdict.rule == "unrated_delegate"
    assert "unrated" in verdict.reason


def test_unrated_downgrades_a_limited_verdict_to_blocked():
    """Precedence attributes the reason; restriction decides the outcome.

    A sensitive permission is LIMITED by rule 2, but rule 3 runs afterwards and
    must still block it — otherwise "unrated delegates get nothing regardless"
    would not hold.
    """
    delegator = agent(permissions=["bank:transfer", "bank:read"])
    to_rated = decide_permission("bank:transfer", delegator, rated())
    to_unrated = decide_permission("bank:transfer", delegator, unrated())

    assert to_rated.decision == LIMITED
    assert to_unrated.decision == BLOCKED
    assert to_unrated.rule == "unrated_delegate"
    assert to_unrated.granted_as is None


def test_confinement_still_wins_over_unrated_for_attribution():
    """Both would block; the first rule in precedence order names the reason."""
    verdict = decide_permission("bank:admin", agent(permissions=[]), unrated())
    assert (verdict.decision, verdict.rule) == (BLOCKED, "confinement")


@pytest.mark.parametrize("events", range(MIN_BASELINE_EVENTS))
def test_the_unrated_boundary(events):
    delegate = agent("delegate-agent", (), events=events)
    verdict = decide_permission("fx:read", agent(permissions=["fx:read"]), delegate)
    assert verdict.decision == BLOCKED


def test_at_the_threshold_a_delegate_becomes_eligible():
    delegate = agent("delegate-agent", (), events=MIN_BASELINE_EVENTS)
    verdict = decide_permission("fx:read", agent(permissions=["fx:read"]), delegate)
    assert verdict.decision == ALLOWED


# --- whole-delegation summary -----------------------------------------------
def test_all_allowed_is_an_allowed_delegation():
    delegator = agent(permissions=["fx:read", "ledger:reconcile"])
    event = make_event(permissions_used=["fx:read", "ledger:reconcile"])
    result = evaluate_delegation(event, delegator, rated())
    assert result.decision == ALLOWED
    assert result.granted_permissions == ["fx:read", "ledger:reconcile"]


def test_a_mixed_outcome_is_limited_not_blocked():
    delegator = agent(permissions=["fx:read", "bank:transfer", "bank:read"])
    event = make_event(permissions_used=["fx:read", "bank:transfer"])
    result = evaluate_delegation(event, delegator, rated())
    assert result.decision == LIMITED
    assert result.granted_permissions == ["fx:read", "bank:read"]
    assert "1 reduced" in result.reason


def test_everything_refused_is_a_blocked_delegation():
    delegator = agent(permissions=["bank:transfer", "bank:admin"])
    event = make_event(permissions_used=["bank:transfer", "bank:admin"])
    result = evaluate_delegation(event, delegator, unrated())
    assert result.decision == BLOCKED
    assert result.granted_permissions == []


def test_the_headline_reason_names_the_dominant_blocking_rule():
    """Both permissions here are refused by rule 3, so that is what is reported."""
    delegator = agent(permissions=["fx:read", "ledger:reconcile"])
    event = make_event(permissions_used=["fx:read", "ledger:reconcile"])
    result = evaluate_delegation(event, delegator, unrated())
    assert result.decision == BLOCKED
    assert "unrated_delegate" in result.reason
    assert {v.rule for v in result.verdicts} == {"unrated_delegate"}


def test_requesting_nothing_is_allowed_and_grants_nothing():
    result = evaluate_delegation(make_event(), agent(permissions=["fx:read"]), rated())
    assert result.decision == ALLOWED
    assert result.granted_permissions == []
    assert "no permissions were requested" in result.reason


def test_every_requested_permission_gets_its_own_verdict():
    delegator = agent(permissions=["fx:read", "bank:transfer", "bank:read"])
    event = make_event(permissions_used=["fx:read", "bank:transfer", "db:read_pii"])
    result = evaluate_delegation(event, delegator, rated())

    assert [v.permission for v in result.verdicts] == [
        "fx:read",
        "bank:transfer",
        "db:read_pii",
    ]
    assert [v.decision for v in result.verdicts] == [ALLOWED, LIMITED, BLOCKED]
    for verdict in result.verdicts:
        assert verdict.rule and verdict.reason


def test_permission_decisions_serialise_for_jsonb_storage():
    delegator = agent(permissions=["bank:transfer", "bank:read"])
    result = evaluate_delegation(make_event(["bank:transfer"]), delegator, rated())
    rows = result.permission_decisions()
    assert rows == [
        {
            "permission": "bank:transfer",
            "decision": LIMITED,
            "rule": "sensitive_category",
            "reason": rows[0]["reason"],
            "granted_as": "bank:read",
        }
    ]


def test_granted_permissions_never_exceed_what_was_requested_in_privilege():
    """The output set must never contain a permission the delegator lacks."""
    delegator = agent(permissions=["bank:transfer", "bank:read", "fx:read"])
    event = make_event(permissions_used=["bank:transfer", "fx:read", "bank:admin"])
    result = evaluate_delegation(event, delegator, rated())
    assert set(result.granted_permissions) <= delegator.known_permissions


def test_the_headline_rule_is_deterministic_when_rules_tie():
    """Regression: `max` over a set is hash-order sensitive.

    The same delegation recorded a different reason depending on the process's
    hash seed, which is unacceptable in a row written once and read as evidence.
    Ties now break by rule precedence.
    """
    verdicts = [
        PermissionVerdict("a", BLOCKED, "sensitive_category", "r"),
        PermissionVerdict("b", BLOCKED, "confinement", "r"),
    ]
    reasons = {_summarise(["a", "b"], verdicts, unrated())[1] for _ in range(50)}
    assert len(reasons) == 1
    # confinement precedes sensitive_category, so it wins the tie
    assert "confinement" in reasons.pop()


def test_the_dominant_rule_still_wins_over_precedence():
    """Precedence only breaks ties; a clear majority is still the headline."""
    verdicts = [
        PermissionVerdict("a", BLOCKED, "confinement", "r"),
        PermissionVerdict("b", BLOCKED, "unrated_delegate", "r"),
        PermissionVerdict("c", BLOCKED, "unrated_delegate", "r"),
    ]
    _, reason = _summarise(["a", "b", "c"], verdicts, unrated())
    assert "unrated_delegate" in reason


# --- layering: an operator's word counts as vouching ------------------------
def test_an_operator_classification_satisfies_the_rating_rule():
    """The two layers must agree about what "vouched for" means.

    Accepting history but not an operator's word would let an operator permit a
    conversation and still be unable to let anything travel through it.
    """
    delegator = agent(permissions=["fx:read"])
    delegate = agent("partner-agent", (), events=0)

    without = decide_permission("fx:read", delegator, delegate)
    assert without.decision == BLOCKED
    assert without.rule == "unrated_delegate"

    for level in ("internal", "external_trusted"):
        with_trust = decide_permission(
            "fx:read", delegator, delegate, delegate_trust=level
        )
        assert with_trust.decision == ALLOWED, level


def test_an_untrusted_classification_does_not_satisfy_the_rating_rule():
    delegator = agent(permissions=["fx:read"])
    delegate = agent("shady-agent", (), events=0)
    verdict = decide_permission(
        "fx:read", delegator, delegate, delegate_trust="external_untrusted"
    )
    assert verdict.decision == BLOCKED
    # Since Phase 5 this is attributed to the more precise rule: an untrusted
    # (or contained) delegate is refused whatever its history, which the rating
    # rule alone could not express — an agent with plenty of history would have
    # passed it.
    assert verdict.rule == "untrusted_delegate"


def test_an_upstream_block_replaces_only_the_rating_rule():
    """Confinement still fires; only the redundant check is displaced."""
    from app.services.delegation_policy import UpstreamBlock

    upstream = UpstreamBlock(
        decision_id="abc-123", rule="untrusted_party", reason="why"
    )
    delegator = agent(permissions=["fx:read"])
    delegate = rated()

    unheld = decide_permission("bank:admin", delegator, delegate, upstream=upstream)
    assert unheld.rule == "confinement", "delegation's own reasoning must survive"

    held = decide_permission("fx:read", delegator, delegate, upstream=upstream)
    assert held.rule == "upstream_a2a_block"
    assert "abc-123" in held.reason
    assert held.decision == BLOCKED
