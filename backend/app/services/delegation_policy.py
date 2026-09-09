"""Least-privilege policy for agent-to-agent delegation.

When an agent hands work to another agent, the dangerous default is for the
delegate to inherit the delegator's full authority. This engine refuses that
default: every requested permission is judged on its own, and a delegate
receives the smallest set that still lets the task proceed.

Three rules, in precedence order:

    1. confinement       A delegator cannot pass on authority it does not hold.
    2. sensitive_category Permissions touching customer data, administrative
                         control, money movement or egress are never auto-granted,
                         even when the delegator holds them.
    3. unrated_delegate  A delegate with too little history to be judged receives
                         nothing at all.

**Precedence resolves attribution; restriction resolves the outcome.** Rules run
in the order above and the first BLOCKED is terminal, but a LIMITED verdict is
provisional — a later rule may still downgrade it to BLOCKED, never upgrade it.
That is what makes rule 3 hold "regardless of what's requested": a sensitive
permission heading to an unrated delegate is LIMITED by rule 2 and then blocked
outright by rule 3.

The delegator's authority is derived exactly as ``baseline.py`` derives typical
behaviour — from the permissions it has actually exercised in events that count
toward a baseline. Blocked and suspicious events are excluded there, so an agent
cannot bootstrap authority by attempting something it was refused.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.schemas.a2a import TrustLevel
from app.schemas.delegation import DelegationDecision, PermissionDecision
from app.services.baseline import AgentBaseline, compute_baseline
from app.services.identity import trust_level_of
from app.services.trust import unrated_reason

# Permission families that are never handed on automatically.
#
# Keywords must occupy a whole segment, delimited by the usual permission
# separators or the ends of the string. An earlier version anchored only the
# left side, which classified "report:transferable_summary" as money movement
# because "transferable" starts with "transfer" — the kind of false positive
# that teaches analysts to stop trusting a decision.
#
# The trade-off is deliberate: this list is precise rather than greedy, so a
# sensitive permission whose name matches no keyword falls through to rule 1 and
# is granted if the delegator holds it. These patterns are the policy's tuning
# surface and are expected to grow with an organisation's permission vocabulary.
_SEGMENT = r"(^|[:._\-/]){}(?=$|[:._\-/])"

SENSITIVE_CATEGORIES: dict[str, re.Pattern] = {
    "customer_data": re.compile(
        _SEGMENT.format(r"(?:pii|customer|customers|personal|gdpr|ssn|kyc)"), re.I
    ),
    "administrative": re.compile(
        _SEGMENT.format(r"(?:admin|root|superuser|grant|impersonate)"), re.I
    ),
    "money_movement": re.compile(
        _SEGMENT.format(r"(?:transfer|payout|wire|settle|disburse)"), re.I
    ),
    "egress": re.compile(_SEGMENT.format(r"(?:egress|external|exfil|upload)"), re.I),
}

# Specific permissions the keyword families do not catch. Widening a keyword to
# reach them is the wrong tool: adding "payment" to money_movement would also
# capture `finance:request_payment`, which has no reduced form and is delegated
# on the ordinary invoice path — the policy would block the primary workflow to
# classify one permission correctly.
#
# Note what is deliberately absent: `db:read_payment`. It is the safe reduction
# of the write, and a reduction the policy grants cannot itself be a permission
# the policy considers unsafe to grant.
EXPLICIT_CATEGORIES: dict[str, str] = {
    "db:write_payment": "money_movement",
}

# Categories with no safe reduced form: there is no "read-only" version of
# administrative control or of moving money out of the building.
NO_SAFE_REDUCTION = frozenset({"administrative", "egress"})

# Explicit reductions take priority over the derived ones below.
EXPLICIT_REDUCTIONS: dict[str, str] = {
    "bank:transfer": "bank:read",
    "db:write_payment": "db:read_payment",
    "db:read_pii": "db:read_masked",
}

# Verbs that imply mutation, and the read-only verb they reduce to.
_WRITE_VERBS = ("write", "create", "update", "delete", "modify", "execute", "initiate")


# Order the rules are applied in, and the tie-break order when several refuse
# the same delegation. Lower runs (and wins attribution) first.
_RULE_PRECEDENCE = {
    "confinement": 0,
    "sensitive_category": 1,
    "untrusted_delegate": 2,
    "unrated_delegate": 3,
    "upstream_a2a_block": 4,
}


@dataclass
class UpstreamBlock:
    """An interaction the A2A layer already refused.

    Delegation is downstream of that gate: if the two agents should not be
    talking, there is nothing to decide about what authority travels. Rather
    than re-deriving the same conclusion under a second rule name, delegation
    cites this — one event, one explanation, two layers.
    """

    decision_id: str
    rule: str
    reason: str


@dataclass
class PermissionVerdict:
    """The decision on a single requested permission, with its justification."""

    permission: str
    decision: str
    rule: str
    reason: str
    granted_as: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "permission": self.permission,
            "decision": self.decision,
            "rule": self.rule,
            "reason": self.reason,
            "granted_as": self.granted_as,
        }


@dataclass
class DelegationVerdict:
    """The decision on a whole delegation."""

    delegator_id: str
    delegate_id: str
    requested_permissions: list[str]
    granted_permissions: list[str]
    decision: str
    reason: str
    verdicts: list[PermissionVerdict] = field(default_factory=list)

    def permission_decisions(self) -> list[dict[str, Any]]:
        return [v.as_dict() for v in self.verdicts]


# --- helpers ----------------------------------------------------------------
def extract_requested_permissions(event) -> list[str]:
    """What the delegate is asking to receive.

    ``metadata.requested_permissions`` is the explicit signal. When it is absent
    the permissions exercised on the delegation event are used instead, which is
    what every emitter predating this feature sends — so existing traffic is
    governed without a backfill or a schema change.
    """
    metadata = event.event_metadata or {}
    raw = metadata.get("requested_permissions")
    if not isinstance(raw, list):
        raw = event.permissions_used or []

    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        cleaned = item.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            out.append(cleaned)
    return out


def categorise(permission: str) -> str | None:
    """The sensitive category a permission falls into, if any.

    Exact names are checked before keyword families, so a permission whose name
    does not advertise what it touches can still be classified.
    """
    if permission in EXPLICIT_CATEGORIES:
        return EXPLICIT_CATEGORIES[permission]
    for name, pattern in SENSITIVE_CATEGORIES.items():
        if pattern.search(permission):
            return name
    return None


def reduced_form(permission: str) -> str | None:
    """The read-only form of a permission, or None if it has none."""
    if permission in EXPLICIT_REDUCTIONS:
        return EXPLICIT_REDUCTIONS[permission]
    for verb in _WRITE_VERBS:
        if re.search(rf"(^|[:._\-/]){verb}(?=$|[:._\-/])", permission, re.I):
            candidate = re.sub(
                rf"(^|[:._\-/]){verb}(?=$|[:._\-/])",
                lambda m: f"{m.group(1)}read",
                permission,
                count=1,
                flags=re.I,
            )
            return candidate if candidate != permission else None
    return None


# --- the rules --------------------------------------------------------------
def _rule_confinement(
    permission: str, delegator: AgentBaseline
) -> PermissionVerdict | None:
    """A delegator cannot pass on authority it does not itself hold."""
    if permission in delegator.known_permissions:
        return None
    return PermissionVerdict(
        permission=permission,
        decision=PermissionDecision.BLOCKED.value,
        rule="confinement",
        reason=(
            f"{delegator.agent_id} has never held {permission!r}, so it cannot "
            "delegate it"
        ),
        granted_as=None,
    )


def _rule_sensitive_category(
    permission: str, delegator: AgentBaseline
) -> PermissionVerdict | None:
    """Sensitive permissions are never auto-granted, even when held."""
    category = categorise(permission)
    if category is None:
        return None

    if category in NO_SAFE_REDUCTION:
        return PermissionVerdict(
            permission=permission,
            decision=PermissionDecision.BLOCKED.value,
            rule="sensitive_category",
            reason=(
                f"{permission!r} is {category.replace('_', ' ')} authority, which "
                "has no reduced form that can be safely delegated"
            ),
            granted_as=None,
        )

    reduced = reduced_form(permission)
    if reduced is None:
        return PermissionVerdict(
            permission=permission,
            decision=PermissionDecision.BLOCKED.value,
            rule="sensitive_category",
            reason=(
                f"{permission!r} touches {category.replace('_', ' ')} and has no "
                "reduced form to grant instead"
            ),
            granted_as=None,
        )

    # A reduction is only a grant if the delegator holds it too — otherwise the
    # downgrade would quietly manufacture authority out of nothing, defeating
    # rule 1 through the back door.
    if reduced not in delegator.known_permissions:
        return PermissionVerdict(
            permission=permission,
            decision=PermissionDecision.BLOCKED.value,
            rule="sensitive_category",
            reason=(
                f"{permission!r} touches {category.replace('_', ' ')}; the reduced "
                f"form {reduced!r} would be granted instead, but {delegator.agent_id} "
                "does not hold that either"
            ),
            granted_as=None,
        )

    return PermissionVerdict(
        permission=permission,
        decision=PermissionDecision.LIMITED.value,
        rule="sensitive_category",
        reason=(
            f"{permission!r} touches {category.replace('_', ' ')} and is not "
            f"auto-granted; reduced to {reduced!r}"
        ),
        granted_as=reduced,
    )


def _rule_untrusted_delegate(
    permission: str, delegate: AgentBaseline, delegate_trust: str
) -> PermissionVerdict | None:
    """A delegate classified untrusted — or suspended — receives nothing.

    Failing the "vouched for" check below is not sufficient on its own: that
    rule lets an agent through on accrued history, and a suspended agent has
    plenty of history. Containment has to refuse regardless of how much.
    """
    if delegate_trust != TrustLevel.EXTERNAL_UNTRUSTED.value:
        return None
    return PermissionVerdict(
        permission=permission,
        decision=PermissionDecision.BLOCKED.value,
        rule="untrusted_delegate",
        reason=(
            f"{delegate.agent_id} is classified external_untrusted; no authority "
            "may be delegated to it whatever its history"
        ),
        granted_as=None,
    )


def _rule_unrated_delegate(
    permission: str, delegate: AgentBaseline, delegate_trust: str = "unrated"
) -> PermissionVerdict | None:
    """A delegate nobody can vouch for receives nothing.

    "Vouched for" means the same thing here as it does at the interaction
    layer: enough clean history, *or* an operator classification. Accepting
    history but not an operator's word would have the two layers disagree about
    one fact — an operator could permit the conversation and still be unable to
    let anything travel through it.
    """
    if delegate_trust in (
        TrustLevel.INTERNAL.value,
        TrustLevel.EXTERNAL_TRUSTED.value,
    ):
        return None
    if delegate.is_established:
        return None
    return PermissionVerdict(
        permission=permission,
        decision=PermissionDecision.BLOCKED.value,
        rule="unrated_delegate",
        reason=unrated_reason(
            delegate.agent_id,
            delegate.event_count,
            "too little history to delegate any authority to",
        ),
        granted_as=None,
    )


def _rule_upstream_block(permission: str, upstream: UpstreamBlock) -> PermissionVerdict:
    """The interaction was already refused, so nothing travels with it."""
    return PermissionVerdict(
        permission=permission,
        decision=PermissionDecision.BLOCKED.value,
        rule="upstream_a2a_block",
        reason=(
            f"the interaction itself was refused by interaction policy "
            f"({upstream.rule}, decision {upstream.decision_id}): {upstream.reason}"
        ),
        granted_as=None,
    )


def decide_permission(
    permission: str,
    delegator: AgentBaseline,
    delegate: AgentBaseline,
    upstream: UpstreamBlock | None = None,
    delegate_trust: str = "unrated",
) -> PermissionVerdict:
    """Judge one permission against every rule.

    Rules run in precedence order. A BLOCKED verdict is terminal; a LIMITED one
    is provisional and a later rule may still downgrade it.
    """
    verdict = PermissionVerdict(
        permission=permission,
        decision=PermissionDecision.ALLOWED.value,
        rule="default_allow",
        reason=f"{delegator.agent_id} holds {permission!r} and it is not restricted",
        granted_as=permission,
    )

    # Confinement and sensitive_category always run: an upstream block says the
    # agents should not be talking, not that the delegator suddenly holds
    # permissions it does not. Only the third rule is replaced, because
    # "the counterparty cannot be vouched for" is precisely what A2A already
    # said — re-deriving it would be the same finding twice under two names.
    # When interaction policy already refused, its citation replaces the
    # counterparty checks below — they would restate the same conclusion. When
    # it did not, both run: untrusted first, because containment refuses
    # regardless of history, and only then the rating check.
    counterparty_rules = (
        (lambda: _rule_upstream_block(permission, upstream),)
        if upstream is not None
        else (
            lambda: _rule_untrusted_delegate(permission, delegate, delegate_trust),
            lambda: _rule_unrated_delegate(permission, delegate, delegate_trust),
        )
    )

    for rule in (
        lambda: _rule_confinement(permission, delegator),
        lambda: _rule_sensitive_category(permission, delegator),
        *counterparty_rules,
    ):
        finding = rule()
        if finding is None:
            continue
        verdict = finding
        if finding.decision == PermissionDecision.BLOCKED.value:
            break  # nothing later can loosen a block

    return verdict


def evaluate_delegation(
    event,
    delegator: AgentBaseline,
    delegate: AgentBaseline,
    upstream: UpstreamBlock | None = None,
    delegate_trust: str = "unrated",
) -> DelegationVerdict:
    """Decide a whole delegation event."""
    requested = extract_requested_permissions(event)
    verdicts = [
        decide_permission(
            p, delegator, delegate, upstream=upstream, delegate_trust=delegate_trust
        )
        for p in requested
    ]
    granted = [v.granted_as for v in verdicts if v.granted_as is not None]

    decision, reason = _summarise(requested, verdicts, delegate, upstream)

    return DelegationVerdict(
        delegator_id=delegator.agent_id,
        delegate_id=delegate.agent_id,
        requested_permissions=requested,
        granted_permissions=granted,
        decision=decision,
        reason=reason,
        verdicts=verdicts,
    )


def _summarise(
    requested: list[str],
    verdicts: list[PermissionVerdict],
    delegate: AgentBaseline,
    upstream: UpstreamBlock | None = None,
) -> tuple[str, str]:
    """The headline decision and the rule that best explains it."""
    if upstream is not None:
        # The gate closed before this layer had a question to answer. Say so
        # once, pointing at the decision that closed it, rather than restating
        # its reasoning as though delegation had reached it independently.
        return (
            DelegationDecision.BLOCKED.value,
            (
                f"refused upstream by interaction policy ({upstream.rule}, "
                f"decision {upstream.decision_id}); no authority was delegated"
            ),
        )

    if not requested:
        return (
            DelegationDecision.ALLOWED.value,
            "no permissions were requested, so nothing was delegated",
        )

    blocked = [v for v in verdicts if v.decision == PermissionDecision.BLOCKED.value]
    limited = [v for v in verdicts if v.decision == PermissionDecision.LIMITED.value]

    if len(blocked) == len(verdicts):
        # Every permission refused. Name the rule responsible for the most of
        # them, so the headline reason matches the dominant cause.
        #
        # Ties break by rule precedence, never by iteration order: `max` over a
        # set is sensitive to string hashing, so the same delegation recorded a
        # different reason depending on the process's hash seed. That is
        # unacceptable in a row that is written once and read as evidence.
        rules = [v.rule for v in blocked]
        dominant = min(
            set(rules), key=lambda r: (-rules.count(r), _RULE_PRECEDENCE.get(r, 99), r)
        )
        return (
            DelegationDecision.BLOCKED.value,
            f"all {len(verdicts)} requested permission(s) refused ({dominant})",
        )

    if blocked or limited:
        parts = []
        if blocked:
            parts.append(f"{len(blocked)} blocked")
        if limited:
            parts.append(f"{len(limited)} reduced")
        return (
            DelegationDecision.LIMITED.value,
            f"{len(requested)} requested: "
            + ", ".join(parts)
            + " under least privilege",
        )

    return (
        DelegationDecision.ALLOWED.value,
        f"all {len(requested)} requested permission(s) held by the delegator "
        "and none restricted",
    )


def decide(
    session: Session, event, upstream: UpstreamBlock | None = None
) -> DelegationVerdict:
    """Entry point used at ingest: derive both baselines, then decide.

    Both sides are derived the same way agent behaviour is derived everywhere
    else, so "what this agent may delegate" and "what this agent normally does"
    can never drift apart.
    """
    delegator = compute_baseline(
        session, event.actor_id, before=event.timestamp, exclude_event_id=event.event_id
    )
    delegate = compute_baseline(
        session,
        event.target_id,
        before=event.timestamp,
        exclude_event_id=event.event_id,
    )
    return evaluate_delegation(
        event,
        delegator,
        delegate,
        upstream=upstream,
        delegate_trust=trust_level_of(session, event.target_id),
    )
