import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import AlertDetail from '../components/AlertDetail'
import StatTile from '../components/StatTile'
import StatusBadge from '../components/StatusBadge'
import { Empty, ErrorState, Loading } from '../components/States'
import { useFetch } from '../hooks/useFetch'
import { getAlerts } from '../lib/api'
import { relativeTime } from '../lib/format'

const RULES = ['unseen_counterparty', 'value_excursion', 'new_permission']
const SEVERITIES = ['high', 'medium', 'low']

function Select({ label, value, onChange, options, allLabel }) {
  return (
    <label className="flex items-center gap-2 text-xs text-ink-faint">
      {label}
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="rounded border border-edge bg-raised px-2 py-1 text-xs text-ink"
      >
        <option value="">{allLabel}</option>
        {options.map((o) => (
          <option key={o} value={o}>{o.replace(/_/g, ' ')}</option>
        ))}
      </select>
    </label>
  )
}

export default function Alerts() {
  const { alertId } = useParams()
  const navigate = useNavigate()
  const [rule, setRule] = useState('')
  const [severity, setSeverity] = useState('')
  const [selectedId, setSelectedId] = useState(alertId ?? null)

  const { data, error, loading, reload } = useFetch(
    () => getAlerts({ rule_name: rule, severity }),
    [rule, severity]
  )

  // Default to the newest alert so the panel is never empty on arrival.
  useEffect(() => {
    if (!data || data.length === 0) return
    if (!selectedId || !data.some((a) => a.alert_id === selectedId)) {
      setSelectedId(data[0].alert_id)
    }
  }, [data, selectedId])

  if (loading) return <Loading label="Loading alerts" />
  if (error) return <ErrorState message={error} onRetry={reload} />

  // The API returns newest-first, which is the right default for a log but the
  // wrong one for a triage queue: severity leads, recency breaks ties.
  const SEVERITY_RANK = { high: 0, medium: 1, low: 2 }
  const alerts = [...(data ?? [])].sort(
    (a, b) =>
      (SEVERITY_RANK[a.severity] ?? 9) - (SEVERITY_RANK[b.severity] ?? 9) ||
      new Date(b.triggered_at) - new Date(a.triggered_at)
  )
  const selected = alerts.find((a) => a.alert_id === selectedId) ?? null
  const high = alerts.filter((a) => a.severity === 'high').length
  const agents = new Set(alerts.map((a) => a.event.actor_id)).size

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold">Alerts</h1>
        <p className="mt-0.5 text-sm text-ink-muted">
          Every rule that fired, recorded at the moment it fired. Each alert keeps
          the evidence that produced it, so a later shift in the agent's baseline
          cannot rewrite history.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile label="Alerts" value={alerts.length} tone={alerts.length ? 'warning' : 'good'} />
        <StatTile label="High severity" value={high} tone={high ? 'critical' : 'good'} icon={high ? '▲' : '✓'} />
        <StatTile label="Agents implicated" value={agents} />
        <StatTile label="Rules firing" value={new Set(alerts.map((a) => a.rule_name)).size} note={`of ${RULES.length} active`} />
      </div>

      <div className="flex flex-wrap items-center gap-4 rounded-lg border border-edge bg-panel px-3 py-2">
        <Select label="Rule" value={rule} onChange={setRule} options={RULES} allLabel="all rules" />
        <Select label="Severity" value={severity} onChange={setSeverity} options={SEVERITIES} allLabel="any severity" />
        {(rule || severity) && (
          <button
            onClick={() => { setRule(''); setSeverity('') }}
            className="text-xs text-accent hover:underline"
          >
            clear filters
          </button>
        )}
        <span className="ml-auto text-xs text-ink-faint">
          {alerts.length} shown
        </span>
      </div>

      {alerts.length === 0 ? (
        <Empty title="No alerts match" hint="Try clearing the filters, or seed the platform." />
      ) : (
        <div className="grid gap-4 lg:grid-cols-[1fr_22rem]">
          <ul className="divide-y divide-edge overflow-hidden rounded-lg border border-edge">
            {alerts.map((a) => {
              const active = a.alert_id === selectedId
              return (
                <li key={a.alert_id}>
                  <button
                    onClick={() => {
                      setSelectedId(a.alert_id)
                      navigate(`/alerts/${a.alert_id}`, { replace: true })
                    }}
                    className={`flex w-full items-start gap-3 px-4 py-3 text-left transition-colors ${
                      active ? 'bg-raised' : 'bg-surface hover:bg-panel/70'
                    }`}
                  >
                    <span className="pt-0.5">
                      <StatusBadge kind="severity" value={a.severity} size="sm" />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="flex flex-wrap items-center gap-x-2 text-sm">
                        <span className="font-medium">{a.event.actor_id}</span>
                        <span className="text-ink-faint">→</span>
                        <span className="text-ink-muted">{a.event.target_id}</span>
                      </span>
                      <span className="mt-0.5 block truncate text-xs text-ink-muted">
                        {a.details?.reason}
                      </span>
                    </span>
                    <span className="whitespace-nowrap pt-0.5 text-xs text-ink-faint">
                      {relativeTime(a.triggered_at)}
                    </span>
                  </button>
                </li>
              )
            })}
          </ul>
          <div>
            <AlertDetail alert={selected} />
          </div>
        </div>
      )}
    </div>
  )
}
