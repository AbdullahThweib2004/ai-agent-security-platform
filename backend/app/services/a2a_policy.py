"""Agent-to-agent interaction policy.

Delegation asks what authority travels with a handoff. This asks the question
that comes first: should these two agents be interacting at all? A verdict here
is about identity and trust, independent of anything being requested.

Rules, in precedence order:

    untrusted_party        either side is external_untrusted -> blocked
    unrated_counterparty   the target has too little history to vouch for
                           -> blocked
    (default)              anything else -> allowed

Scope: agent -> agent only. A user directing an agent is not agent-to-agent
traffic, and a tool, API or database is not an identity that can be trusted or
distrusted.

Recording, not enforcing
------------------------
A verdict here does not change the event's ``platform_status``. That is
deliberate. This platform observes traffic after the fact rather than sitting in
the request path, so every other decision it makes — alerts, delegation verdicts
— is a record rather than an intervention, and interaction policy is no
different. It also avoids a feedback loop that has bitten this codebase twice:
flagged events are excluded from baselines, so a policy that flags an agent's
first contact would remove that counterparty from the baseline, making every
later contact look novel and blocking the whole relationship forever. The
decision is durable and queryable; it is not a poison pill for the data the
decision depends on.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.schemas.a2a import TrustLevel
from app.services.baseline import compute_baseline
from app.services.identity import trust_level_of
from app.services.trust import is_rated, unrated_reason

ALLOWED = "allowed"
BLOCKED = "blocked"

# Lower runs first, and wins attribution when more than one would refuse.
_RULE_PRECEDENCE = {
    "untrusted_party": 0,
    "unrated_counterparty": 1,
    "default_allow": 2,
}


@dataclass
class A2AVerdict:
    """The decision on one interaction, with the trust levels it was made under."""

    requester_id: str
    target_id: str
    decision: str
    rule: str
    reason: str
    requester_trust: str
    target_trust: str

    @property
    def is_blocked(self) -> bool:
        return self.decision == BLOCKED


def applies_to(event) -> bool:
    """Whether interaction policy has anything to say about this event.

    Agent to agent only. Everything else is an agent using a resource, which
    other layers already govern.
    """
    return event.actor_type == "agent" and event.target_type == "agent"


def _rule_untrusted_party(
    requester_id: str, target_id: str, requester_trust: str, target_trust: str
) -> tuple[str, str] | None:
    """An operator has explicitly marked one side untrusted.

    Both directions are checked. An untrusted agent reaching in is at least as
    dangerous as a trusted agent reaching out to one, and unlike the rating
    below this is an explicit assertion, so there is no cold-start cost to
    enforcing it symmetrically.
    """
    untrusted = TrustLevel.EXTERNAL_UNTRUSTED.value
    offenders = []
    if requester_trust == untrusted:
        offenders.append(f"requester {requester_id}")
    if target_trust == untrusted:
        offenders.append(f"target {target_id}")
    if not offenders:
        return None
    return (
        "untrusted_party",
        f"{' and '.join(offenders)} classified external_untrusted; "
        "interaction refused regardless of what was requested",
    )


def _rule_unrated_counterparty(
    target_id: str, target_trust: str, target_clean_events: int
) -> tuple[str, str] | None:
    """Nobody can vouch for the target yet.

    Only the target is judged on rating, not the requester. An agent's own first
    actions are how it earns a history; refusing them would mean no agent could
    ever become rated, and the anomaly rules already decline to judge a
    cold-start actor for the same reason.

    An explicit classification satisfies this: an operator vouching for an agent
    is exactly the evidence the rating threshold is a proxy for.
    """
    if target_trust in (
        TrustLevel.INTERNAL.value,
        TrustLevel.EXTERNAL_TRUSTED.value,
    ):
        return None
    if is_rated(target_clean_events):
        return None
    return (
        "unrated_counterparty",
        unrated_reason(
            target_id,
            target_clean_events,
            "too little history to accept an interaction with",
        ),
    )


def evaluate(
    *,
    requester_id: str,
    target_id: str,
    requester_trust: str,
    target_trust: str,
    target_clean_events: int,
) -> A2AVerdict:
    """Judge one interaction. Pure: no database, so every branch is testable."""
    for finding in (
        _rule_untrusted_party(requester_id, target_id, requester_trust, target_trust),
        _rule_unrated_counterparty(target_id, target_trust, target_clean_events),
    ):
        if finding is not None:
            rule, reason = finding
            return A2AVerdict(
                requester_id=requester_id,
                target_id=target_id,
                decision=BLOCKED,
                rule=rule,
                reason=reason,
                requester_trust=requester_trust,
                target_trust=target_trust,
            )

    return A2AVerdict(
        requester_id=requester_id,
        target_id=target_id,
        decision=ALLOWED,
        rule="default_allow",
        reason=(
            f"{requester_id} ({requester_trust}) may interact with "
            f"{target_id} ({target_trust})"
        ),
        requester_trust=requester_trust,
        target_trust=target_trust,
    )


def decide(session: Session, event) -> A2AVerdict:
    """Entry point used at ingest: resolve both identities, then judge.

    The target's rating comes from the same baseline computation every other
    layer uses, so "rated" means one thing across the platform.
    """
    requester_trust = trust_level_of(session, event.actor_id)
    target_trust = trust_level_of(session, event.target_id)

    target_baseline = compute_baseline(
        session,
        event.target_id,
        before=event.timestamp,
        exclude_event_id=event.event_id,
    )

    return evaluate(
        requester_id=event.actor_id,
        target_id=event.target_id,
        requester_trust=requester_trust,
        target_trust=target_trust,
        target_clean_events=target_baseline.event_count,
    )
