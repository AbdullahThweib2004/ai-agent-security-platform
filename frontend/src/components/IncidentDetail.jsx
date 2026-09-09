import { useEffect, useState } from 'react'
import StatusBadge from './StatusBadge'
import { Loading } from './States'
import { getEvents, resolveIncident } from '../lib/api'
import { duration, shortTime } from '../lib/format'

const LAYER_LABEL = {
  alert: 'Anomaly rules',
  delegation: 'Delegation policy',
  a2a: 'Interaction policy',
}

/** What was recorded while an agent was contained — proof it was not dropped. */
function ContainedActivity({ agentId }) {
  const [events, setEvents] = useState(null)
  useEffect(() => {
    let cancelled = false
    getEvents({ actor_id: agentId, limit: 1000 })
      .then((rows) => !cancelled && setEvents(rows.filter((e) => e.actor_suspended)))
      .catch(() => !cancelled && setEvents([]))
    return () => {
      cancelled = true
    }
  }, [agentId])

  if (events === null) return <Loading label="Loading activity" />
  if (events.length === 0)
    return (
      <p className="text-[11px] italic text-ink-faint">
        Nothing recorded since containment began.
      </p>
    )

  return (
    <div>
      <p className="text-[11px] leading-relaxed text-ink-muted">
        <span className="font-medium text-status-warning">{events.length}</span> event
        {events.length === 1 ? '' : 's'} recorded while contained — kept, not dropped.
        Containment marks an agent's activity; it never creates a blind spot in the
        record.
      </p>
      <ul className="mt-2 space-y-1">
        {events.slice(0, 5).map((e) => (
          <li key={e.event_id} className="flex items-center gap-2 text-[11px]">
            <span className="text-status-warning" aria-hidden="true">⊘</span>
            <span className="text-ink-faint">{shortTime(e.timestamp)}</span>
            <span className="font-mono text-ink-muted">{e.action_type}</span>
            <span className="text-ink-faint">→</span>
            <span className="truncate text-ink-muted">{e.target_id}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

export default function IncidentDetail({ incident, loading, onResolved }) {
  const [operator, setOperator] = useState('')
  const [note, setNote] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    setOperator('')
    setNote('')
    setError(null)
  }, [incident?.incident_id])

  if (loading) return <Loading label="Loading incident" />
  if (!incident)
    return (
      <div className="rounded-lg border border-dashed border-edge bg-panel p-6 text-center text-xs text-ink-faint">
        Select an incident to see what contained the agent.
      </div>
    )

  const grouped = incident.evidence_by_layer ?? {}
  const summary = incident.trigger_summary ?? {}

  async function submit(event) {
    event.preventDefault()
    setBusy(true)
    setError(null)
    try {
      await resolveIncident(incident.incident_id, {
        resolved_by: operator.trim(),
        note: note.trim() || null,
      })
      onResolved?.()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-4 rounded-lg border border-edge bg-panel p-4">
      <div>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="text-sm font-medium">{incident.agent_id}</div>
            <div className="mt-1 text-[11px] text-ink-faint">
              severity {incident.severity}
            </div>
          </div>
          <StatusBadge kind="incident" value={incident.status} size="sm" />
        </div>
        <p className="mt-2 text-xs leading-relaxed text-ink-muted">{summary.reason}</p>
      </div>

      {/* Event time beside detection time. The gap is a real signal: a large one
          means the signals were replayed or backfilled, which is exactly the
          case that makes windowing on ingest time wrong. */}
      <div className="border-t border-edge pt-3">
        <div className="mb-2 text-xs font-semibold text-ink-muted">Timing</div>
        <div className="grid grid-cols-2 gap-3">
          <div>
            <div className="text-[11px] text-ink-faint">Agent acted</div>
            <div className="tabular mt-0.5 text-xs">{shortTime(incident.opened_at)}</div>
          </div>
          <div>
            <div className="text-[11px] text-ink-faint">Platform noticed</div>
            <div className="tabular mt-0.5 text-xs">{shortTime(incident.detected_at)}</div>
          </div>
        </div>
        <div className="mt-2 flex items-center gap-2 rounded border border-edge bg-surface px-2 py-1.5">
          <span className="text-[11px] text-ink-faint">Detection lag</span>
          <span
            className={`tabular text-xs font-medium ${
              incident.detection_lag_seconds > 3600
                ? 'text-status-warning'
                : 'text-status-good'
            }`}
          >
            {duration(incident.detection_lag_seconds)}
          </span>
          {incident.detection_lag_seconds > 3600 && (
            <span className="text-[11px] text-ink-faint">
              — signals were replayed or backfilled
            </span>
          )}
        </div>
      </div>

      <div className="border-t border-edge pt-3">
        <div className="mb-2 flex items-baseline justify-between">
          <span className="text-xs font-semibold text-ink-muted">
            Signals by layer
          </span>
          <span className="text-[11px] text-ink-faint">
            {summary.signal_count} across {summary.event_count} event
            {summary.event_count === 1 ? '' : 's'}
          </span>
        </div>
        <ul className="space-y-2">
          {Object.entries(grouped).map(([layer, links]) => (
            <li key={layer} className="rounded border border-edge bg-surface p-2.5">
              <div className="flex items-center justify-between gap-2">
                <span className="text-[11px] font-medium text-ink">
                  {LAYER_LABEL[layer] ?? layer}
                </span>
                <code className="font-mono text-[10px] text-ink-faint">{layer}</code>
              </div>
              {links.map((link) => (
                <p key={link.event_id} className="mt-1 text-[11px] leading-relaxed text-ink-muted">
                  {link.detail}
                </p>
              ))}
            </li>
          ))}
        </ul>
        <p className="mt-2 text-[11px] italic text-ink-faint">
          Independent layers agreeing is the threshold — not how many times one
          layer fired.
        </p>
      </div>

      <div className="border-t border-edge pt-3">
        <div className="mb-2 text-xs font-semibold text-ink-muted">
          Activity while contained
        </div>
        <ContainedActivity agentId={incident.agent_id} />
      </div>

      <div className="border-t border-edge pt-3">
        {incident.status === 'resolved' ? (
          <div className="text-[11px] text-ink-muted">
            Released by{' '}
            <span className="font-medium text-ink">{incident.resolved_by}</span> on{' '}
            {shortTime(incident.resolved_at)}
            {incident.resolution_note && (
              <p className="mt-1 italic text-ink-faint">“{incident.resolution_note}”</p>
            )}
          </div>
        ) : (
          <form onSubmit={submit} className="space-y-2">
            <div className="text-xs font-semibold text-ink-muted">Release the agent</div>
            {/* Containment opens automatically; it closes only when a named
                person says so. Auto-expiry would let a compromised agent
                quietly return. */}
            <p className="text-[11px] leading-relaxed text-ink-faint">
              Containment lifts only by an explicit, attributed operator action.
            </p>
            <input
              value={operator}
              onChange={(e) => setOperator(e.target.value)}
              placeholder="your name or handle"
              required
              className="w-full rounded border border-edge bg-surface px-2 py-1 text-xs text-ink placeholder:text-ink-faint focus:border-accent focus:outline-none"
            />
            <input
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="why (optional)"
              className="w-full rounded border border-edge bg-surface px-2 py-1 text-xs text-ink placeholder:text-ink-faint focus:border-accent focus:outline-none"
            />
            {error && <p className="text-[11px] text-status-critical">{error}</p>}
            <button
              type="submit"
              disabled={busy || !operator.trim()}
              className="w-full rounded border border-edge bg-raised px-3 py-1.5 text-xs text-ink-muted hover:border-accent hover:text-ink disabled:cursor-not-allowed disabled:opacity-40"
            >
              {busy ? 'Releasing…' : 'Resolve and release'}
            </button>
          </form>
        )}
      </div>
    </div>
  )
}
