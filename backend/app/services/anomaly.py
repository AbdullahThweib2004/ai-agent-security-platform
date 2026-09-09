"""Rule-based anomaly detection.

Deliberately no ML in this phase. Three rules, each explainable in one sentence
to whoever reads the alert:

  unseen_counterparty  the agent contacted something it has never contacted
  value_excursion      the action's value sits well outside its usual range
  new_permission       the agent used a permission it has never used

Every rule is evaluated against a baseline built from the agent's own prior
events, and every finding carries the evidence that produced it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.schemas.alert import AlertRule, AlertSeverity
from app.services.baseline import AgentBaseline, extract_value

# How far past its historical maximum a value must go before it counts as an
# excursion. 1.0 would flag every new record high; 1.5 asks for a real jump.
VALUE_TOLERANCE = 1.5

# Alternative trigger for agents with a stable, tight distribution.
STDEV_MULTIPLIER = 3.0


@dataclass
class Finding:
    """One rule firing, with the evidence behind it."""

    rule_name: str
    severity: str
    details: dict[str, Any] = field(default_factory=dict)


def _rule_unseen_counterparty(event, baseline: AgentBaseline) -> Finding | None:
    if event.target_id in baseline.known_targets:
        return None
    # Reaching another agent for the first time is the more serious case: that
    # is lateral movement between agents, not just a new tool in the toolbox.
    severity = (
        AlertSeverity.HIGH.value
        if event.target_type == "agent"
        else AlertSeverity.MEDIUM.value
    )
    return Finding(
        rule_name=AlertRule.UNSEEN_COUNTERPARTY.value,
        severity=severity,
        details={
            "reason": (
                f"{event.actor_id} contacted {event.target_id} "
                f"({event.target_type}) for the first time"
            ),
            "target_id": event.target_id,
            "target_type": event.target_type,
            "known_targets": sorted(baseline.known_targets),
            "baseline_event_count": baseline.event_count,
        },
    )


def _rule_value_excursion(event, baseline: AgentBaseline) -> Finding | None:
    value = extract_value(event.event_metadata)
    if value is None or not baseline.values:
        return None

    baseline_max = baseline.value_max or 0.0
    mean = baseline.value_mean
    stdev = baseline.value_stdev

    threshold = baseline_max * VALUE_TOLERANCE
    trigger = "max_x_tolerance"

    # A tight distribution can be violated well below max * tolerance; take
    # whichever threshold is lower so neither pattern is missed.
    if mean is not None and stdev:
        sigma_threshold = mean + STDEV_MULTIPLIER * stdev
        if sigma_threshold < threshold:
            threshold = sigma_threshold
            trigger = "mean_plus_3_stdev"

    if value <= threshold:
        return None

    ratio = round(value / baseline_max, 2) if baseline_max else None
    severity = (
        AlertSeverity.HIGH.value
        if ratio is not None and ratio >= 5
        else AlertSeverity.MEDIUM.value
    )
    return Finding(
        rule_name=AlertRule.VALUE_EXCURSION.value,
        severity=severity,
        details={
            "reason": (
                f"value {value:,.2f} exceeds {event.actor_id}'s usual range "
                f"(historical max {baseline_max:,.2f})"
            ),
            "observed_value": value,
            "threshold": round(threshold, 2),
            "trigger": trigger,
            "baseline_min": baseline.value_min,
            "baseline_max": baseline_max,
            "baseline_mean": round(mean, 2) if mean is not None else None,
            "baseline_stdev": round(stdev, 2) if stdev is not None else None,
            "samples": len(baseline.values),
            "ratio_to_max": ratio,
        },
    )


def _rule_new_permission(event, baseline: AgentBaseline) -> Finding | None:
    used = list(event.permissions_used or [])
    if not used:
        return None
    novel = [p for p in used if p not in baseline.known_permissions]
    if not novel:
        return None
    return Finding(
        rule_name=AlertRule.NEW_PERMISSION.value,
        severity=(
            AlertSeverity.HIGH.value if len(novel) > 1 else AlertSeverity.MEDIUM.value
        ),
        details={
            "reason": (
                f"{event.actor_id} used permission(s) not seen before: "
                f"{', '.join(novel)}"
            ),
            "new_permissions": novel,
            "permissions_used": used,
            "known_permissions": sorted(baseline.known_permissions),
        },
    )


RULES = (
    _rule_unseen_counterparty,
    _rule_value_excursion,
    _rule_new_permission,
)


def evaluate(event, baseline: AgentBaseline) -> list[Finding]:
    """Run every rule against one event. Returns findings, worst first.

    An agent without an established baseline is not judged: with too little
    history everything is novel, and the resulting alert storm would train
    whoever reads it to ignore alerts.
    """
    if not baseline.is_established:
        return []

    findings = [f for rule in RULES if (f := rule(event, baseline)) is not None]
    order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: order.get(f.severity, 9))
    return findings
