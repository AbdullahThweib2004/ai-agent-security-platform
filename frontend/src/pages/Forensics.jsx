import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import StatTile from '../components/StatTile'
import TimelineEntry from '../components/TimelineEntry'
import { Empty, ErrorState, Loading } from '../components/States'
import { useFetch } from '../hooks/useFetch'
import { getAlerts, getTimeline } from '../lib/api'
import { ENTITY_ICON, shortTime } from '../lib/format'
import { railClass } from '../lib/ramp'
import { rampStep } from '../components/StatusBadge'

/** With no event chosen, offer the flagged ones as entry points. */
function IncidentPicker() {
  const navigate = useNavigate()
  const [eventId, setEventId] = useState('')
  const { data, error, loading, reload } = useFetch(() => getAlerts({ limit: 50 }), [])

  if (loading) return <Loading label="Loading flagged events" rows={5} />
  if (error) return <ErrorState message={error} onRetry={reload} />

  const alerts = data ?? []
  const seen = new Set()
  const incidents = alerts.filter((a) => {
    if (seen.has(a.event.event_id)) return false
    seen.add(a.event.event_id)
    return true
  })

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-title font-semibold">Forensics</h1>
        <p className="mt-1 max-w-[70ch] text-body text-ink-muted">
          Pick any event and this reconstructs the whole chain it belongs to —
          backward to the root cause, forward through everything it set off.
        </p>
      </div>

      <form
        onSubmit={(e) => {
          e.preventDefault()
          if (eventId.trim()) navigate(`/forensics/${eventId.trim()}`)
        }}
        className="flex gap-2 rounded-lg border border-edge bg-panel p-3"
      >
        <input
          value={eventId}
          onChange={(e) => setEventId(e.target.value)}
          placeholder="Paste an event_id to investigate…"
          className="flex-1 rounded border border-edge bg-surface px-3 py-1.5 font-mono text-label text-ink transition-colors placeholder:font-sans placeholder:text-ink-faint hover:border-edge-strong"
        />
        <button
          type="submit"
          className="rounded border border-edge bg-raised px-3 py-1.5 text-label font-medium text-ink transition-colors hover:border-accent"
        >
          Reconstruct
        </button>
      </form>

      <div>
        <h2 className="mb-2 text-heading font-semibold text-ink-muted">Flagged events</h2>
        {incidents.length === 0 ? (
          <Empty title="Nothing flagged yet" hint="Seed the platform to generate an incident." />
        ) : (
          <ul className="divide-y divide-edge overflow-hidden rounded-lg border border-edge">
            {incidents.map((a) => (
              <li key={a.event.event_id}>
                <button
                  onClick={() => navigate(`/forensics/${a.event.event_id}`)}
                  className={`flex w-full items-center gap-3 px-3 py-2.5 text-left font-mono text-body ${railClass(
                    rampStep('severity', a.severity)
                  )}`}
                >
                  <span aria-hidden="true" className="text-ink-faint">
                    {ENTITY_ICON[a.event.actor_type]}
                  </span>
                  <span className="font-medium">{a.event.actor_id}</span>
                  <span aria-hidden="true" className="text-ink-faint">→</span>
                  <span className="text-ink-muted">{a.event.target_id}</span>
                  <span className="ml-auto font-mono text-label text-ink-faint">
                    {shortTime(a.event.timestamp)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

export default function Forensics() {
  const { eventId } = useParams()
  const navigate = useNavigate()
  const { data, error, loading, reload } = useFetch(
    () => (eventId ? getTimeline(eventId) : Promise.resolve(null)),
    [eventId]
  )

  if (!eventId) return <IncidentPicker />
  if (loading) return <Loading label="Reconstructing the event chain" rows={6} />
  if (error) return <ErrorState message={error} onRetry={reload} />
  if (!data) return <Empty title="Nothing to show" />

  const entries = data.entries ?? []

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-title font-semibold">Incident timeline</h1>
          <p className="mt-1 max-w-[70ch] text-body text-ink-muted">
            Reconstructed from{' '}
            <span className="font-mono text-label text-ink">{eventId.slice(0, 8)}…</span> by
            walking to the root cause and forward through every event it set off.
          </p>
        </div>
        <button
          onClick={() => navigate('/forensics')}
          className="rounded border border-edge bg-raised px-3 py-1.5 text-label text-ink-muted transition-colors hover:border-accent hover:text-ink"
        >
          Choose another incident
        </button>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile label="Events in chain" value={data.event_count} />
        <StatTile
          label="Alerts in chain"
          value={data.alert_count}
          tone={data.alert_count ? 'critical' : 'good'}
          icon={data.alert_count ? '▲' : '✓'}
        />
        <StatTile label="Participants" value={data.participants?.length ?? 0} />
        <StatTile
          label="Window"
          value={`${new Date(data.started_at).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })}`}
          note={`to ${new Date(data.ended_at).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })} · ${shortTime(data.started_at).split(',')[0]}`}
        />
      </div>

      <div className="rounded-lg border border-edge bg-panel px-3 py-2">
        <span className="text-micro uppercase text-ink-faint">Participants</span>
        <div className="mt-1 flex flex-wrap gap-2">
          {data.participants?.map((p) => (
            <span key={p} className="rounded border border-edge bg-raised px-2 py-0.5 font-mono text-label text-ink-muted">
              {p}
            </span>
          ))}
        </div>
      </div>

      <ol className="mt-2">
        {entries.map((entry, i) => (
          <TimelineEntry
            key={entry.event.event_id}
            entry={entry}
            isLast={i === entries.length - 1}
          />
        ))}
      </ol>
    </div>
  )
}
