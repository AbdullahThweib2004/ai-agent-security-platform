"""Incident detection: when do independent signals add up to containment?

Three layers already flag things: the anomaly rules, delegation policy, and
interaction policy. This asks whether what they have flagged about one agent, in
one window, is enough to stop it acting.

The threshold
-------------
Within a 24h window, an agent trips containment when either holds:

  * signals from **two or more independent layers, across two or more
    distinct events**, or
  * **two or more high-severity alerts**.

The distinct-event requirement on the first branch matters: two layers reacting
to one action is not corroboration across behaviour, it is two opinions about a
single act, and one anomalous act is what the alert layer already exists for.
The severity branch carries no such requirement, because two high-severity
alerts on one action is a smoking gun and waiting for a second action would be
waiting for more damage.

Corroboration, not volume, is the discriminator. The three layers were built to
reason independently, so agreement between them is real evidence; one layer
firing repeatedly is usually an artefact. Counting alone opens incidents on
``analyst-1`` — a human whose delegation was refused by ``confinement`` during
ordinary operation — and on ``finance-agent``'s benign cold-start blocks. On the
seeded corpus this rule fires on exactly one agent-day out of five: the actual
attacker, which carried signals from all three layers at once.

Windowing on event time
-----------------------
The window is measured on ``agent_events.timestamp``, never on when a signal was
recorded. ``alerts.triggered_at`` and the decision timestamps are stamped at
ingest, so a replay, a backfill, or a reconciliation pass lands the whole history
in one instant. Windowed on those, every agent looks like a simultaneous burst
and cold-start artefacts from two weeks earlier become indistinguishable from a
live attack.

Attribution
-----------
Signals count against the **actor**, not the target. A block naming a
counterparty is often a fact about the actor — ``confinement`` says the delegator
lacks a permission, and attributing it to the delegate would open an incident on
a perfectly healthy agent that merely received a refused request.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.a2a import A2ADecision
from app.models.delegation import Delegation
from app.models.event import AgentEvent, Alert
from app.models.incident import Incident, IncidentEvent
from app.services.baseline import compute_baseline
from app.services.trust import is_rated

WINDOW = timedelta(hours=24)
MIN_DISTINCT_LAYERS = 2
MIN_DISTINCT_EVENTS = 2
MIN_HIGH_SEVERITY_ALERTS = 2

LAYER_ALERT = "alert"
LAYER_DELEGATION = "delegation"
LAYER_A2A = "a2a"


@dataclass
class Signal:
    """One flagged thing an agent did, as the detector sees it."""

    event_id: object
    layer: str
    at: datetime
    detail: str
    high_severity: bool = False


@dataclass
class Assessment:
    """Whether an agent's recent signals amount to an incident."""

    agent_id: str
    signals: list[Signal] = field(default_factory=list)
    window_start: datetime | None = None
    window_end: datetime | None = None

    @property
    def layers(self) -> set[str]:
        return {s.layer for s in self.signals}

    @property
    def high_alert_count(self) -> int:
        return sum(1 for s in self.signals if s.high_severity)

    @property
    def event_ids(self) -> set:
        return {s.event_id for s in self.signals}

    @property
    def corroborated(self) -> bool:
        """Independent layers agreeing about more than one action.

        Both conditions matter. Two layers reacting to a *single* event is not
        corroboration across behaviour — it is two views of one action, and one
        anomalous action is what the alert layer is already for. Requiring
        distinct events makes this a judgement about a pattern.
        """
        return (
            len(self.layers) >= MIN_DISTINCT_LAYERS
            and len(self.event_ids) >= MIN_DISTINCT_EVENTS
        )

    @property
    def trips(self) -> bool:
        # The severity branch deliberately has no distinct-event requirement:
        # two high-severity alerts on one action is a smoking gun, not a
        # pattern, and waiting for a second action would be waiting for more
        # damage.
        return self.corroborated or self.high_alert_count >= MIN_HIGH_SEVERITY_ALERTS

    @property
    def reason(self) -> str:
        if self.corroborated:
            return (
                f"{len(self.signals)} signals from {len(self.layers)} independent "
                f"layers ({', '.join(sorted(self.layers))}) across "
                f"{len(self.event_ids)} events within 24h"
            )
        return (
            f"{self.high_alert_count} high-severity alerts within 24h "
            f"({len(self.signals)} signals total)"
        )

    def summary(self) -> dict:
        return {
            "signal_count": len(self.signals),
            "event_count": len(self.event_ids),
            "layers": sorted(self.layers),
            "high_alert_count": self.high_alert_count,
            "window_start": (
                self.window_start.isoformat() if self.window_start else None
            ),
            "window_end": self.window_end.isoformat() if self.window_end else None,
            "reason": self.reason,
            "threshold": {
                "min_distinct_layers": MIN_DISTINCT_LAYERS,
                "min_distinct_events": MIN_DISTINCT_EVENTS,
                "min_high_severity_alerts": MIN_HIGH_SEVERITY_ALERTS,
                "window_hours": int(WINDOW.total_seconds() // 3600),
            },
        }


def _is_only_an_upstream_citation(delegation) -> bool:
    """Whether a blocked delegation added no reasoning of its own.

    If every permission verdict points upstream, the delegation layer reached no
    independent conclusion — it relayed one. If any permission was refused by
    confinement or sensitive_category, the layer *did* reason independently and
    the signal counts.
    """
    verdicts = delegation.permission_decisions or []
    if not verdicts:
        return False
    return all(v.get("rule") == "upstream_a2a_block" for v in verdicts)


def assess(session: Session, agent_id: str, as_of: datetime) -> Assessment:
    """Gather an agent's signals in the 24h of event time ending at ``as_of``."""
    window_start = as_of - WINDOW
    assessment = Assessment(
        agent_id=agent_id, window_start=window_start, window_end=as_of
    )

    def in_window(stmt):
        return stmt.where(
            AgentEvent.actor_id == agent_id,
            AgentEvent.timestamp > window_start,
            AgentEvent.timestamp <= as_of,
        )

    alerts = session.execute(
        in_window(
            select(Alert, AgentEvent.timestamp).join(
                AgentEvent, AgentEvent.event_id == Alert.event_id
            )
        )
    ).all()
    for alert, at in alerts:
        assessment.signals.append(
            Signal(
                event_id=alert.event_id,
                layer=LAYER_ALERT,
                at=at,
                detail=f"{alert.rule_name} ({alert.severity})",
                high_severity=alert.severity == "high",
            )
        )

    blocked_delegations = session.execute(
        in_window(
            select(Delegation, AgentEvent.timestamp).join(
                AgentEvent, AgentEvent.event_id == Delegation.event_id
            )
        ).where(Delegation.decision == "blocked")
    ).all()
    for delegation, at in blocked_delegations:
        # A delegation refused *only* because interaction policy already refused
        # the conversation is not a second, independent finding — it is the same
        # finding, cited. Counting both would make one conclusion look like two
        # corroborating layers, which is exactly the corroboration this
        # threshold treats as evidence.
        #
        # This is the same principle the Phase 4 layering established: cite the
        # upstream block, do not re-derive it. Here it means: do not re-count it.
        if _is_only_an_upstream_citation(delegation):
            continue
        assessment.signals.append(
            Signal(
                event_id=delegation.event_id,
                layer=LAYER_DELEGATION,
                at=at,
                detail=f"delegation to {delegation.delegate_id} blocked",
            )
        )

    blocked_interactions = session.execute(
        in_window(
            select(A2ADecision, AgentEvent.timestamp).join(
                AgentEvent, AgentEvent.event_id == A2ADecision.event_id
            )
        ).where(A2ADecision.decision == "blocked")
    ).all()
    for decision, at in blocked_interactions:
        assessment.signals.append(
            Signal(
                event_id=decision.event_id,
                layer=LAYER_A2A,
                at=at,
                detail=f"interaction with {decision.target_id} blocked ({decision.rule})",
            )
        )

    return assessment


def open_incident(session: Session, assessment: Assessment) -> Incident:
    """Record the containment decision and the evidence behind it."""
    incident = Incident(
        agent_id=assessment.agent_id,
        status="open",
        # Event time, not now: the incident is dated to when the agent acted.
        opened_at=assessment.window_end,
        severity="high" if assessment.high_alert_count else "medium",
        trigger_summary=assessment.summary(),
    )
    session.add(incident)
    session.flush()

    # Write-once evidence. One event can contribute through several layers, and
    # that corroboration is the whole basis of the decision, so the link is
    # keyed on (incident, event, layer) rather than collapsing to one row.
    # The key is (incident, event, layer), so several signals of the same layer
    # on one event share a row. Their details are joined rather than dropped —
    # three alerts on one action is materially different from one, and the
    # evidence should say so.
    grouped: dict[tuple, list[str]] = {}
    for signal in assessment.signals:
        grouped.setdefault((signal.event_id, signal.layer), []).append(signal.detail)

    for (event_id, layer), details in grouped.items():
        session.add(
            IncidentEvent(
                incident_id=incident.incident_id,
                event_id=event_id,
                layer=layer,
                detail="; ".join(details),
            )
        )
    session.flush()
    return incident


def evaluate(session: Session, event) -> Incident | None:
    """Ingest hook: assess the actor, and contain it if the threshold is met.

    Returns the incident that was opened, or None. An agent already under
    containment is left alone — the partial unique index would refuse a second
    open incident anyway, and re-opening on every further signal would turn
    "is this agent suspended" into a question about counting rows.
    """
    from app.services.incidents import active_incident

    if active_incident(session, event.actor_id) is not None:
        return None

    # An agent the platform cannot yet judge must not be contained on the
    # strength of that ignorance. During cold start the layers produce signals
    # that describe what the platform does not know rather than what the agent
    # did: interaction policy refuses an unrated counterparty, and delegation
    # refuses a permission the delegator has not yet been observed exercising.
    # Two such artefacts look exactly like two independent layers agreeing.
    #
    # This is the same threshold the anomaly rules, delegation and interaction
    # policy already defer to — the fourth consumer of trust.is_rated, not a
    # fourth definition. Containment is the most severe action here, so it is
    # the last place that should fire on absence of evidence.
    actor_baseline = compute_baseline(
        session, event.actor_id, before=event.timestamp, exclude_event_id=event.event_id
    )
    if not is_rated(actor_baseline.event_count):
        return None

    assessment = assess(session, event.actor_id, event.timestamp)
    if not assessment.trips:
        return None
    return open_incident(session, assessment)
