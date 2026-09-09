import StatusBadge from './StatusBadge'
import { ENTITY_ICON, clockTime, money } from '../lib/format'
import { HEALTH_COLOR, STATUS_COLOR } from '../lib/palette'

const RELATION = {
  self: { label: 'the event you asked about', cls: 'border-accent/60 bg-accent/10 text-accent' },
  ancestor: { label: 'led to it', cls: 'border-edge bg-raised text-ink-muted' },
  descendant: { label: 'caused by it', cls: 'border-edge bg-raised text-ink-muted' },
  related: { label: 'same incident, other branch', cls: 'border-status-warning/50 bg-status-warning/10 text-status-warning' },
}

const DOT = {
  suspicious: STATUS_COLOR.critical,
  blocked: STATUS_COLOR.warning,
  allowed: '#3b465e',
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
          style={{ background: DOT[event.platform_status] ?? DOT.allowed }}
          aria-hidden="true"
        />
      </div>

      <div className="flex-1 pb-4" style={{ paddingLeft: `${Math.min(depth, 6) * 18}px` }}>
        <div
          className={`rounded-lg border p-3 ${
            isSelf ? 'border-accent/50 bg-accent/5' : 'border-edge bg-panel'
          }`}
        >
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
            <span className="tabular text-xs text-ink-faint">{clockTime(event.timestamp)}</span>
            <span className="flex items-center gap-1.5 font-medium">
              <span aria-hidden="true" className="text-ink-faint">{ENTITY_ICON[event.actor_type]}</span>
              {event.actor_id}
            </span>
            <span className="font-mono text-xs text-ink-faint">—{event.action_type}→</span>
            <span className="flex items-center gap-1.5 text-ink-muted">
              <span aria-hidden="true" className="text-ink-faint">{ENTITY_ICON[event.target_type]}</span>
              {event.target_id}
            </span>
            <span className="ml-auto flex items-center gap-2">
              {amount != null && (
                <span
                  className="tabular text-xs font-medium"
                  style={{ color: alerts.some((a) => a.rule_name === 'value_excursion') ? HEALTH_COLOR.suspicious : '#98a2b8' }}
                >
                  {money(amount)}
                </span>
              )}
              <StatusBadge kind="status" value={event.platform_status} size="sm" />
            </span>
          </div>

          <div className="mt-2 flex flex-wrap items-center gap-2">
            <span className={`rounded border px-1.5 py-0.5 text-[11px] ${rel.cls}`}>
              {rel.label}
            </span>
            <span className="text-[11px] text-ink-faint">depth {depth}</span>
            {event.permissions_used?.length > 0 && (
              <span className="flex flex-wrap gap-1">
                {event.permissions_used.map((p) => (
                  <span key={p} className="rounded bg-raised px-1.5 py-0.5 font-mono text-[11px] text-ink-muted">
                    {p}
                  </span>
                ))}
              </span>
            )}
          </div>

          {alerts.length > 0 && (
            <ul className="mt-2 space-y-1 border-t border-edge pt-2">
              {alerts.map((a) => (
                <li key={a.alert_id} className="flex items-start gap-2">
                  <StatusBadge kind="severity" value={a.severity} size="sm" />
                  <span className="text-xs text-ink-muted">{a.details?.reason}</span>
                </li>
              ))}
            </ul>
          )}

          {event.reported_status !== event.platform_status && (
            <p className="mt-2 text-[11px] text-status-serious">
              Caller reported this as “{event.reported_status}”; the platform
              graded it “{event.platform_status}”.
            </p>
          )}
        </div>
      </div>
    </li>
  )
}
