import { Link } from 'react-router-dom'
import StatusBadge from './StatusBadge'
import { ENTITY_ICON, money, shortTime } from '../lib/format'

const RULE_TITLE = {
  unseen_counterparty: 'Unseen counterparty',
  value_excursion: 'Value excursion',
  new_permission: 'New permission used',
}

// Evidence keys worth surfacing as labelled rows, in reading order. Anything
// else in `details` still renders below, so a new rule needs no UI change.
const EVIDENCE_LABELS = {
  observed_value: 'Observed value',
  threshold: 'Threshold',
  baseline_min: 'Baseline min',
  baseline_max: 'Baseline max',
  baseline_mean: 'Baseline mean',
  baseline_stdev: 'Baseline stdev',
  samples: 'Samples',
  ratio_to_max: 'Ratio to max',
  trigger: 'Triggered by',
  target_id: 'Target',
  target_type: 'Target type',
  known_targets: 'Known targets',
  new_permissions: 'New permissions',
  permissions_used: 'Permissions used',
  known_permissions: 'Known permissions',
  baseline_event_count: 'Baseline events',
}

const MONEY_KEYS = new Set([
  'observed_value', 'threshold', 'baseline_min', 'baseline_max', 'baseline_mean',
])

function renderValue(key, value) {
  if (Array.isArray(value)) {
    if (value.length === 0) return <span className="italic text-ink-faint">none</span>
    return (
      <div className="flex flex-wrap justify-end gap-1">
        {value.map((v) => (
          <span key={v} className="rounded bg-raised px-1.5 py-0.5 font-mono text-[11px]">
            {v}
          </span>
        ))}
      </div>
    )
  }
  if (typeof value === 'number' && MONEY_KEYS.has(key)) return money(value)
  if (typeof value === 'number') return value.toLocaleString()
  return String(value)
}

export default function AlertDetail({ alert }) {
  if (!alert)
    return (
      <div className="rounded-lg border border-dashed border-edge bg-panel p-6 text-center text-xs text-ink-faint">
        Select an alert to see the evidence behind it.
      </div>
    )

  const { details = {}, event } = alert
  const { reason, ...evidence } = details
  const ordered = [
    ...Object.keys(EVIDENCE_LABELS).filter((k) => k in evidence),
    ...Object.keys(evidence).filter((k) => !(k in EVIDENCE_LABELS)),
  ]

  return (
    <div className="space-y-4 rounded-lg border border-edge bg-panel p-4">
      <div>
        <div className="flex items-center justify-between gap-3">
          <h2 className="text-sm font-semibold">
            {RULE_TITLE[alert.rule_name] ?? alert.rule_name}
          </h2>
          <StatusBadge kind="severity" value={alert.severity} size="sm" />
        </div>
        <p className="mt-2 text-xs leading-relaxed text-ink-muted">{reason}</p>
        <div className="mt-2 text-[11px] text-ink-faint">
          Triggered {shortTime(alert.triggered_at)} · rule{' '}
          <span className="font-mono">{alert.rule_name}</span>
        </div>
      </div>

      <div className="border-t border-edge pt-3">
        <div className="mb-2 text-xs font-semibold text-ink-muted">Evidence</div>
        <dl className="space-y-1.5">
          {ordered.map((key) => (
            <div key={key} className="flex items-start justify-between gap-4">
              <dt className="text-xs text-ink-faint">{EVIDENCE_LABELS[key] ?? key}</dt>
              <dd className="tabular max-w-[60%] text-right text-xs text-ink-muted">
                {renderValue(key, evidence[key])}
              </dd>
            </div>
          ))}
        </dl>
      </div>

      <div className="border-t border-edge pt-3">
        <div className="mb-2 text-xs font-semibold text-ink-muted">Triggering event</div>
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="flex items-center gap-1.5">
            <span aria-hidden="true" className="text-ink-faint">
              {ENTITY_ICON[event.actor_type]}
            </span>
            {event.actor_id}
          </span>
          <span className="text-ink-faint">—{event.action_type}→</span>
          <span className="flex items-center gap-1.5">
            <span aria-hidden="true" className="text-ink-faint">
              {ENTITY_ICON[event.target_type]}
            </span>
            {event.target_id}
          </span>
        </div>
        <dl className="mt-2 space-y-1.5">
          <div className="flex justify-between gap-4">
            <dt className="text-xs text-ink-faint">Caller reported</dt>
            <dd><StatusBadge kind="status" value={event.reported_status} size="sm" /></dd>
          </div>
          <div className="flex justify-between gap-4">
            <dt className="text-xs text-ink-faint">Platform verdict</dt>
            <dd><StatusBadge kind="status" value={event.platform_status} size="sm" /></dd>
          </div>
          <div className="flex justify-between gap-4">
            <dt className="text-xs text-ink-faint">Occurred</dt>
            <dd className="text-xs text-ink-muted">{shortTime(event.timestamp)}</dd>
          </div>
        </dl>

        {Object.keys(event.metadata ?? {}).length > 0 && (
          <div className="mt-3">
            <div className="mb-1 text-xs text-ink-faint">Event metadata</div>
            <pre className="overflow-x-auto rounded border border-edge bg-surface p-2 font-mono text-[11px] leading-relaxed text-ink-muted">
{JSON.stringify(event.metadata, null, 2)}
            </pre>
          </div>
        )}
      </div>

      <Link
        to={`/forensics/${event.event_id}`}
        className="block rounded border border-edge bg-raised px-3 py-2 text-center text-xs text-ink-muted hover:border-accent hover:text-ink"
      >
        Investigate in Forensics →
      </Link>
    </div>
  )
}
