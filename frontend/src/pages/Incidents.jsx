import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import IncidentDetail from '../components/IncidentDetail'
import StatTile from '../components/StatTile'
import StatusBadge from '../components/StatusBadge'
import { Empty, ErrorState, Loading } from '../components/States'
import { ClearFilters, FilterBar, SelectFilter, TextFilter } from '../components/Filters'
import { railClass } from '../lib/ramp'
import { rampStep } from '../components/StatusBadge'
import { useFetch } from '../hooks/useFetch'
import { getIncident, getIncidents } from '../lib/api'
import { duration, relativeTime } from '../lib/format'

// Mirrors the API: status is a closed set, so it is a dropdown; agent ids are
// open-ended, so they stay free text.
const STATUSES = ['open', 'contained', 'resolved']

export default function Incidents() {
  const { incidentId } = useParams()
  const navigate = useNavigate()
  const [statusFilter, setStatusFilter] = useState('')
  const [agentId, setAgentId] = useState('')
  const [selectedId, setSelectedId] = useState(incidentId ?? null)
  const [refreshKey, setRefreshKey] = useState(0)

  const { data, error, loading, reload } = useFetch(
    () => getIncidents({ status: statusFilter, agent_id: agentId.trim(), limit: 200 }),
    [statusFilter, agentId, refreshKey]
  )

  const [detail, setDetail] = useState(null)
  const [detailLoading, setDetailLoading] = useState(false)
  useEffect(() => {
    if (!selectedId) {
      setDetail(null)
      return
    }
    let cancelled = false
    setDetailLoading(true)
    getIncident(selectedId)
      .then((d) => !cancelled && setDetail(d))
      .catch(() => !cancelled && setDetail(null))
      .finally(() => !cancelled && setDetailLoading(false))
    return () => {
      cancelled = true
    }
  }, [selectedId, refreshKey])

  useEffect(() => {
    if (!data || data.length === 0) return
    if (!selectedId || !data.some((i) => i.incident_id === selectedId)) {
      setSelectedId(data[0].incident_id)
    }
  }, [data, selectedId])

  if (loading && !data) return <Loading label="Loading incidents" rows={4} />
  if (error) return <ErrorState message={error} onRetry={reload} />

  const rows = data ?? []
  const suspended = rows.filter((i) => i.is_suspended).length
  const hasFilters = statusFilter || agentId

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-title font-semibold">Incidents</h1>
        <p className="mt-1 max-w-[70ch] text-body text-ink-muted">
          Agents contained automatically when independent layers agreed something
          was wrong. Containment opens on its own; only a named operator lifts it.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile label="Incidents" value={rows.length} />
        <StatTile
          label="Agents contained"
          value={suspended}
          tone={suspended ? 'critical' : 'good'}
          icon={suspended ? '⊘' : '✓'}
          note={suspended ? 'suspended now' : 'none suspended'}
        />
        <StatTile
          label="Open"
          value={rows.filter((i) => i.status === 'open').length}
          tone={rows.some((i) => i.status === 'open') ? 'critical' : 'neutral'}
        />
        <StatTile
          label="Resolved"
          value={rows.filter((i) => i.status === 'resolved').length}
          note="released by an operator"
        />
      </div>

      <FilterBar summary={`${rows.length} shown${suspended > 0 ? ` · ${suspended} contained` : ''}`}>
        <SelectFilter
          label="Status"
          value={statusFilter}
          onChange={setStatusFilter}
          options={STATUSES}
          anyLabel="any status"
        />
        <TextFilter label="Agent" value={agentId} onChange={setAgentId} placeholder="any agent" />
        <ClearFilters
          show={Boolean(hasFilters)}
          onClear={() => {
            setStatusFilter('')
            setAgentId('')
          }}
        />
      </FilterBar>

      {rows.length === 0 ? (
        <Empty
          title="No incidents"
          hint={
            hasFilters
              ? 'Try clearing the filters.'
              : 'Nothing has crossed the containment threshold.'
          }
        />
      ) : (
        <div className="grid items-start gap-4 lg:grid-cols-[1fr_24rem]">
          <ul className="divide-y divide-edge overflow-hidden rounded-lg border border-edge">
            {rows.map((row) => {
              const active = row.incident_id === selectedId
              const layers = row.trigger_summary?.layers ?? []
              return (
                <li key={row.incident_id}>
                  <button
                    onClick={() => {
                      setSelectedId(row.incident_id)
                      navigate(`/incidents/${row.incident_id}`, { replace: true })
                    }}
                    className={`flex w-full items-center gap-3 px-3 py-2.5 text-left ${railClass(
                      rampStep('incident', row.status),
                      active
                    )}`}
                  >
                    <StatusBadge kind="incident" value={row.status} size="sm" />
                    <span className="min-w-0 flex-1">
                      <span className="block font-mono text-body font-medium">{row.agent_id}</span>
                      <span className="mt-0.5 block truncate text-label text-ink-muted">
                        {row.trigger_summary?.reason}
                      </span>
                    </span>
                    <span className="hidden shrink-0 gap-1 xl:flex">
                      {layers.map((l) => (
                        <span
                          key={l}
                          className="rounded border border-edge bg-raised px-1.5 py-0.5 font-mono text-micro normal-case tracking-normal text-ink-muted"
                        >
                          {l}
                        </span>
                      ))}
                    </span>
                    {/* Event time vs detection time, at a glance. */}
                    <span className="whitespace-nowrap text-right font-mono text-label">
                      <span className="block text-ink-faint">
                        acted {relativeTime(row.opened_at)}
                      </span>
                      <span
                        className={
                          row.detection_lag_seconds > 3600
                            ? 'text-status-warning'
                            : 'text-ink-faint'
                        }
                      >
                        +{duration(row.detection_lag_seconds)} to detect
                      </span>
                    </span>
                  </button>
                </li>
              )
            })}
          </ul>
          <div>
            <IncidentDetail
              incident={detail}
              loading={detailLoading}
              onResolved={() => setRefreshKey((k) => k + 1)}
            />
          </div>
        </div>
      )}
    </div>
  )
}
