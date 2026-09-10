import { Link } from 'react-router-dom'
import StatusBadge, { rampStep } from './StatusBadge'
import { ENTITY_ICON, clockTime, money } from '../lib/format'
import { status, edgeIdle } from '../lib/palette'
import { railTone } from '../lib/ramp'

const RELATION = {
  self: { label: 'the event you asked about', cls: 'border-accent/60 bg-accent/10 text-accent-ink' },
  ancestor: { label: 'led to it', cls: 'border-edge bg-raised text-ink-muted' },
  descendant: { label: 'caused by it', cls: 'border-edge bg-raised text-ink-muted' },
  related: { label: 'same incident, other branch', cls: 'border-status-warning/50 bg-status-warning/10 text-status-warning' },
}

// Blocked was yellow here while the delegation and interaction badges painted
// the same word red. One meaning, one colour.
const dotColor = (platformStatus) => {
  if (platformStatus === 'suspicious' || platformStatus === 'blocked') return status.critical
  return edgeIdle()
}

// The three policy layers, named the way the Incidents page names them so an
// analyst reads one vocabulary in both places.
const LAYER_LABEL = {
  a2a: 'Interaction policy',
  delegation: 'Delegation policy',
  alert: 'Anomaly rules',
}

/**
 * One layer's verdict on this event. The rail carries the decision's ramp step,
 * so a refusal is visible before the row is read and an `allowed` verdict stays
 * deliberately quiet — the same rule the list views follow.
 *
 * Linked when the layer has a record to open; a plain row when it does not.
 */
function LayerRow({ layer, step, badge, children, to }) {
  const body = (
    <>
      <div className="flex items-baseline justify-between gap-2">
        <span className="flex items-baseline gap-2">
          <span className="text-label font-semibold text-ink">{LAYER_LABEL[layer] ?? layer}</span>
          {badge}
        </span>
        <code className="font-mono text-micro normal-case tracking-normal text-ink-faint">
          {layer}
        </code>
      </div>
      {children}
    </>
  )
  const cls = `block rounded border border-edge bg-surface p-2 ${railTone(step)}`
  return to ? (
    <li>
      <Link to={to} className={`${cls} transition-colors hover:border-accent`}>
        {body}
      </Link>
    </li>
  ) : (
    <li className={cls}>{body}</li>
  )
}

/** Every layer that had something to say about this event, in the order the
 *  platform evaluates them: who may talk to whom, then what authority
 *  travelled, then what the rules noticed afterwards. */
function LayerVerdicts({ entry }) {
  const { alerts = [], delegation, a2a_decision: a2a, incidents = [] } = entry
  if (!delegation && !a2a && alerts.length === 0 && incidents.length === 0) return null

  // The evidence rows are per layer, so several can cite the same incident.
  // Report the incidents, not the row count.
  const byIncident = new Map()
  for (const link of incidents) {
    const seen = byIncident.get(link.incident_id) ?? { ...link, layers: [] }
    seen.layers.push(link.layer)
    byIncident.set(link.incident_id, seen)
  }

  return (
    <div className="mt-2 border-t border-edge pt-2">
      <ul className="space-y-1.5">
        {a2a && (
          <LayerRow
            layer="a2a"
            step={rampStep('interaction', a2a.decision)}
            badge={<StatusBadge kind="interaction" value={a2a.decision} size="sm" />}
            to={a2a.decision_id ? `/interactions/${a2a.decision_id}` : undefined}
          >
            <p className="mt-1 text-micro normal-case tracking-normal leading-relaxed text-ink-muted">
              {a2a.reason}
            </p>
            <div className="mt-1 flex flex-wrap items-center gap-1">
              <StatusBadge kind="trust" value={a2a.requester_trust} size="sm" />
              <span aria-hidden="true" className="text-ink-faint">→</span>
              <StatusBadge kind="trust" value={a2a.target_trust} size="sm" />
            </div>
          </LayerRow>
        )}

        {delegation && (
          <LayerRow
            layer="delegation"
            step={rampStep('delegation', delegation.decision)}
            badge={<StatusBadge kind="delegation" value={delegation.decision} size="sm" />}
            to={
              delegation.delegation_id ? `/delegations/${delegation.delegation_id}` : undefined
            }
          >
            <p className="mt-1 text-micro normal-case tracking-normal leading-relaxed text-ink-muted">
              {delegation.reason}
            </p>
            <div className="mt-1 font-mono text-micro normal-case tracking-normal text-ink-faint">
              <span
                className={
                  delegation.decision === 'blocked'
                    ? 'text-status-critical-ink'
                    : delegation.decision === 'limited'
                      ? 'text-status-warning'
                      : 'text-status-good'
                }
              >
                {delegation.granted_permissions?.length ?? 0}/
                {delegation.requested_permissions?.length ?? 0}
              </span>{' '}
              granted
            </div>
          </LayerRow>
        )}

        {alerts.length > 0 && (
          <LayerRow
            layer="alert"
            // The layer takes its colour from the worst rule that fired.
            step={rampStep(
              'severity',
              ['high', 'medium', 'low'].find((sev) => alerts.some((a) => a.severity === sev))
            )}
            badge={
              <span className="font-mono text-micro normal-case tracking-normal text-ink-faint">
                {alerts.length} signal{alerts.length === 1 ? '' : 's'}
              </span>
            }
          >
            <ul className="mt-1 space-y-1">
              {alerts.map((a) => (
                <li key={a.alert_id} className="flex items-start gap-2">
                  <StatusBadge kind="severity" value={a.severity} size="sm" />
                  <span className="text-micro normal-case tracking-normal leading-relaxed text-ink-muted">
                    {a.details?.reason}
                  </span>
                </li>
              ))}
            </ul>
          </LayerRow>
        )}
      </ul>

      {byIncident.size > 0 && (
        <ul className="mt-1.5 space-y-1">
          {[...byIncident.values()].map((inc) => (
            <li key={inc.incident_id}>
              <Link
                to={`/incidents/${inc.incident_id}`}
                className="flex flex-wrap items-center gap-2 rounded border border-status-critical/40 bg-status-critical/[0.07] px-2 py-1.5 transition-colors hover:border-accent"
              >
                <StatusBadge kind="incident" value={inc.status} size="sm" />
                <span className="text-micro normal-case tracking-normal text-ink-muted">
                  cited as evidence in this agent&rsquo;s incident, across
                </span>
                {inc.layers.map((l) => (
                  <code
                    key={l}
                    className="rounded border border-edge bg-raised px-1 font-mono text-micro normal-case tracking-normal text-ink-muted"
                  >
                    {l}
                  </code>
                ))}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

export default function TimelineEntry({ entry, isLast }) {
  const { event, depth, relation, alerts } = entry
  const rel = RELATION[relation] ?? RELATION.ancestor
  const isSelf = relation === 'self'
  const amount = event.metadata?.amount

  return (
    <li className="relative flex gap-4">
      {/* Rail: the vertical spine plus this event's status dot. */}
      <div className="relative flex w-4 flex-none justify-center">
        {!isLast && <span className="absolute top-5 h-full w-px bg-edge" aria-hidden="true" />}
        <span
          className="relative z-10 mt-3 h-3 w-3 flex-none rounded-full ring-4 ring-surface"
          style={{ background: dotColor(event.platform_status) }}
          aria-hidden="true"
        />
      </div>

      <div className="flex-1 pb-4" style={{ paddingLeft: `${Math.min(depth, 6) * 18}px` }}>
        <div
          className={`rounded-lg border p-3 ${
            isSelf ? 'border-accent/50 bg-accent/5' : 'border-edge bg-panel'
          }`}
        >
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-body">
            <span className="tabular font-mono text-label text-ink-faint">{clockTime(event.timestamp)}</span>
            <span className="flex items-center gap-1.5 font-mono font-medium">
              <span aria-hidden="true" className="text-ink-faint">{ENTITY_ICON[event.actor_type]}</span>
              {event.actor_id}
            </span>
            <span aria-hidden="true" className="font-mono text-label text-ink-faint">—{event.action_type}→</span>
            <span className="flex items-center gap-1.5 font-mono text-ink-muted">
              <span aria-hidden="true" className="text-ink-faint">{ENTITY_ICON[event.target_type]}</span>
              {event.target_id}
            </span>
            <span className="ml-auto flex items-center gap-2">
              {amount != null && (
                <span
                  className={`tabular font-mono text-label font-medium ${
                    alerts.some((a) => a.rule_name === 'value_excursion')
                      ? 'text-status-critical-ink'
                      : 'text-ink-muted'
                  }`}
                >
                  {money(amount)}
                </span>
              )}
              <StatusBadge kind="status" value={event.platform_status} size="sm" />
            </span>
          </div>

          <div className="mt-2 flex flex-wrap items-center gap-2">
            <span className={`rounded border px-1.5 py-0.5 text-micro normal-case tracking-normal ${rel.cls}`}>
              {rel.label}
            </span>
            <span className="font-mono text-micro normal-case tracking-normal text-ink-faint">depth {depth}</span>
            {event.permissions_used?.length > 0 && (
              <span className="flex flex-wrap gap-1">
                {event.permissions_used.map((p) => (
                  <span key={p} className="rounded border border-edge bg-raised px-1.5 py-0.5 font-mono text-micro normal-case tracking-normal text-ink-muted">
                    {p}
                  </span>
                ))}
              </span>
            )}
          </div>

          <LayerVerdicts entry={entry} />

          {event.reported_status !== event.platform_status && (
            <p className="mt-2 text-micro normal-case tracking-normal text-status-serious">
              Caller reported this as “{event.reported_status}”; the platform
              graded it “{event.platform_status}”.
            </p>
          )}
        </div>
      </div>
    </li>
  )
}
