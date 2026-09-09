"""The A2A interaction policy, rule by rule.

No database: a verdict is a pure function of (requester, target, their trust
levels, the target's clean history), which pins each boundary precisely.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.a2a_policy import ALLOWED, BLOCKED, applies_to, evaluate
from app.services.trust import MIN_BASELINE_EVENTS

INTERNAL = "internal"
TRUSTED = "external_trusted"
UNTRUSTED = "external_untrusted"
UNRATED = "unrated"

RATED = MIN_BASELINE_EVENTS + 5


def judge(requester_trust=INTERNAL, target_trust=INTERNAL, target_clean_events=RATED):
    return evaluate(
        requester_id="payment-agent",
        target_id="finance-agent",
        requester_trust=requester_trust,
        target_trust=target_trust,
        target_clean_events=target_clean_events,
    )


# --- scope ------------------------------------------------------------------
@pytest.mark.parametrize(
    "actor_type, target_type, expected",
    [
        ("agent", "agent", True),
        ("user", "agent", False),  # a person directing an agent is not A2A
        ("agent", "tool", False),  # a tool is not an identity
        ("agent", "api", False),
        ("agent", "database", False),
        ("user", "tool", False),
    ],
)
def test_applies_only_to_agent_to_agent(actor_type, target_type, expected):
    event = SimpleNamespace(actor_type=actor_type, target_type=target_type)
    assert applies_to(event) is expected


# --- rule 1: untrusted_party ------------------------------------------------
def test_an_untrusted_target_is_blocked():
    verdict = judge(target_trust=UNTRUSTED)
    assert verdict.decision == BLOCKED
    assert verdict.rule == "untrusted_party"
    assert "target finance-agent" in verdict.reason


def test_an_untrusted_requester_is_blocked():
    """Both directions. An untrusted agent reaching in is at least as dangerous."""
    verdict = judge(requester_trust=UNTRUSTED)
    assert verdict.decision == BLOCKED
    assert verdict.rule == "untrusted_party"
    assert "requester payment-agent" in verdict.reason


def test_both_sides_untrusted_names_both():
    verdict = judge(requester_trust=UNTRUSTED, target_trust=UNTRUSTED)
    assert verdict.decision == BLOCKED
    assert "requester payment-agent and target finance-agent" in verdict.reason


def test_untrusted_blocks_regardless_of_history():
    """A long, clean history does not launder an explicit classification."""
    verdict = judge(target_trust=UNTRUSTED, target_clean_events=10_000)
    assert verdict.decision == BLOCKED
    assert verdict.rule == "untrusted_party"


# --- rule 2: unrated_counterparty -------------------------------------------
@pytest.mark.parametrize("events", range(MIN_BASELINE_EVENTS))
def test_an_unrated_target_is_blocked(events):
    verdict = judge(target_trust=UNRATED, target_clean_events=events)
    assert verdict.decision == BLOCKED
    assert verdict.rule == "unrated_counterparty"
    assert verdict.reason.startswith("finance-agent is unrated")


def test_at_the_rating_threshold_the_interaction_is_allowed():
    verdict = judge(target_trust=UNRATED, target_clean_events=MIN_BASELINE_EVENTS)
    assert verdict.decision == ALLOWED


def test_an_unrated_requester_is_not_blocked():
    """An agent's own first actions are how it earns a history.

    Refusing them would mean no agent could ever become rated — the same reason
    the anomaly rules decline to judge a cold-start actor.
    """
    verdict = judge(requester_trust=UNRATED, target_clean_events=RATED)
    assert verdict.decision == ALLOWED


@pytest.mark.parametrize("level", [INTERNAL, TRUSTED])
def test_an_explicit_classification_satisfies_the_rating_rule(level):
    """An operator vouching for an agent is the evidence the threshold proxies for."""
    verdict = judge(target_trust=level, target_clean_events=0)
    assert verdict.decision == ALLOWED
    assert verdict.rule == "default_allow"


# --- precedence -------------------------------------------------------------
def test_untrusted_outranks_unrated_and_is_deterministic():
    """Both rules would refuse; the higher-precedence one names the reason."""
    reasons = {
        judge(target_trust=UNTRUSTED, target_clean_events=0).rule for _ in range(50)
    }
    assert reasons == {"untrusted_party"}


def test_precedence_holds_when_only_the_requester_is_untrusted():
    verdict = judge(
        requester_trust=UNTRUSTED, target_trust=UNRATED, target_clean_events=0
    )
    assert verdict.rule == "untrusted_party"


# --- default allow ----------------------------------------------------------
def test_internal_to_internal_is_allowed():
    verdict = judge(requester_trust=INTERNAL, target_trust=INTERNAL)
    assert verdict.decision == ALLOWED
    assert verdict.rule == "default_allow"
    assert "internal" in verdict.reason


@pytest.mark.parametrize(
    "requester, target",
    [
        (INTERNAL, TRUSTED),
        (TRUSTED, INTERNAL),
        (TRUSTED, TRUSTED),
        (UNRATED, INTERNAL),
    ],
)
def test_trusted_combinations_are_allowed(requester, target):
    verdict = judge(requester_trust=requester, target_trust=target)
    assert verdict.decision == ALLOWED


def test_the_verdict_snapshots_both_trust_levels():
    """The identity row is mutable and this verdict is not, so it records its basis."""
    verdict = judge(requester_trust=INTERNAL, target_trust=UNTRUSTED)
    assert verdict.requester_trust == INTERNAL
    assert verdict.target_trust == UNTRUSTED


def test_is_blocked_matches_the_decision():
    assert judge(target_trust=UNTRUSTED).is_blocked is True
    assert judge().is_blocked is False
