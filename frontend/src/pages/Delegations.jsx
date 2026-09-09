import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import DelegationDetail from '../components/DelegationDetail'
import StatTile from '../components/StatTile'
import StatusBadge from '../components/StatusBadge'
import { Empty, ErrorState, Loading } from '../components/States'
import { useFetch } from '../hooks/useFetch'
import { getDelegation, getDelegations } from '../lib/api'
import { relativeTime } from '../lib/format'

// The API's own filter design, mirrored: `decision` is a closed enum, so it is
// a dropdown and an invalid value cannot be typed. Agent ids are open-ended,
// so they stay free text.
const DECISIONS = ['allowed', 'limited', 'blocked']

function DecisionFilter({ value, onChange }) {
  return (
    <label className="flex items-center gap-2 text-xs text-ink-faint">
      Decision
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="rounded border border-edge bg-raised px-2 py-1 text-xs text-ink"
      >
        <option value="">any decision</option>
        {DECISIONS.map((d) => (
          <option key={d} value={d}>{d}</option>
        ))}
      </select>
    </label>
  )
}

function TextFilter({ label, value, onChange, placeholder }) {
  return (
    <label className="flex items-center gap-2 text-xs text-ink-faint">
      {label}
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-40 rounded border border-edge bg-raised px-2 py-1 text-xs text-ink placeholder:text-ink-faint focus:border-accent focus:outline-none"
      />
    </label>
  )
}

export default function Delegations() {
  const { delegationId } = useParams()
  const navigate = useNavigate()
  const [decision, setDecision] = useState('')
  const [delegatorId, setDelegatorId] = useState('')
  const [delegateId, setDelegateId] = useState('')
  const [selectedId, setSelectedId] = useState(delegationId ?? null)

  // Filters are query parameters, so every change is a fresh request. Filtering
  // client-side would silently lie once the result set exceeds the page limit.
  const { data, error, loading, reload } = useFetch(
    () =>
      getDelegations({
        decision,
        delegator_id: delegatorId.trim(),
        delegate_id: delegateId.trim(),
        limit: 200,
      }),
    [decision, delegatorId, delegateId]
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
    getDelegation(selectedId)
      .then((d) => !cancelled && setDetail(d))
      .catch(() => !cancelled && setDetail(null))
      .finally(() => !cancelled && setDetailLoading(false))
    return () => {
      cancelled = true
    }
  }, [selectedId])

  useEffect(() => {
    if (!data || data.length === 0) return
    if (!selectedId || !data.some((d) => d.delegation_id === selectedId)) {
      setSelectedId(data[0].delegation_id)
    }
  }, [data, selectedId])

  if (loading && !data) return <Loading label="Loading delegations" />
  if (error) return <ErrorState message={error} onRetry={reload} />

  const rows = data ?? []
  const counts = DECISIONS.reduce(
    (acc, d) => ({ ...acc, [d]: rows.filter((r) => r.decision === d).length }),
    {}
  )
  const restricted = counts.limited + counts.blocked
  const hasFilters = decision || delegatorId || delegateId

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold">Delegations</h1>
        <p className="mt-0.5 text-sm text-ink-muted">
          What authority travelled when one agent handed work to another. A
          delegate receives the least privilege the task needs — never an
          automatic copy of the delegator's permissions.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile label="Delegations" value={rows.length} />
        <StatTile
          label="Allowed in full"
          value={counts.allowed}
          tone="good"
          icon="✓"
          note="nothing was cut"
        />
        <StatTile
          label="Reduced"
          value={counts.limited}
          tone={counts.limited ? 'warning' : 'neutral'}
          icon={counts.limited ? '◆' : undefined}
          note="granted a lesser form"
        />
        <StatTile
          label="Refused"
          value={counts.blocked}
          tone={counts.blocked ? 'critical' : 'good'}
          icon={counts.blocked ? '⊘' : '✓'}
          note="nothing granted"
        />
      </div>

      <div className="flex flex-wrap items-center gap-4 rounded-lg border border-edge bg-panel px-3 py-2">
        <DecisionFilter value={decision} onChange={setDecision} />
        <TextFilter
          label="Delegator"
          value={delegatorId}
          onChange={setDelegatorId}
          placeholder="any agent"
        />
        <TextFilter
          label="Delegate"
          value={delegateId}
          onChange={setDelegateId}
          placeholder="any agent"
        />
        {hasFilters && (
          <button
            onClick={() => {
              setDecision('')
              setDelegatorId('')
              setDelegateId('')
            }}
            className="text-xs text-accent hover:underline"
          >
            clear filters
          </button>
        )}
        <span className="ml-auto text-xs text-ink-faint">
          {rows.length} shown{restricted > 0 && ` · ${restricted} restricted`}
        </span>
      </div>

      {rows.length === 0 ? (
        <Empty
          title="No delegations match"
          hint={
            hasFilters
              ? 'Try clearing the filters.'
              : 'Seed the platform: docker compose exec backend python scripts/simulate.py --reset'
          }
        />
      ) : (
        <div className="grid items-start gap-4 lg:grid-cols-[1fr_24rem]">
          <ul className="divide-y divide-edge overflow-hidden rounded-lg border border-edge">
            {rows.map((row) => {
              const active = row.delegation_id === selectedId
              const requested = row.requested_permissions?.length ?? 0
              const granted = row.granted_permissions?.length ?? 0
              const cut = requested - granted
              return (
                <li key={row.delegation_id}>
                  <button
                    onClick={() => {
                      setSelectedId(row.delegation_id)
                      navigate(`/delegations/${row.delegation_id}`, { replace: true })
                    }}
                    className={`flex w-full items-center gap-3 px-4 py-3 text-left transition-colors ${
                      active ? 'bg-raised' : 'bg-surface hover:bg-panel/70'
                    }`}
                  >
                    <StatusBadge kind="delegation" value={row.decision} size="sm" />
                    <span className="min-w-0 flex-1">
                      <span className="flex flex-wrap items-center gap-x-2 text-sm">
                        <span className="font-medium">{row.delegator_id}</span>
                        <span className="text-ink-faint">→</span>
                        <span className="text-ink-muted">{row.delegate_id}</span>
                      </span>
                      <span className="mt-0.5 block truncate text-xs text-ink-muted">
                        {row.reason}
                      </span>
                    </span>
                    {/* At-a-glance signal: how much authority was withheld. */}
                    <span className="tabular whitespace-nowrap text-right text-xs">
                      <span
                        className={
                          cut === 0
                            ? 'text-status-good'
                            : granted === 0
                              ? 'text-status-critical'
                              : 'text-status-warning'
                        }
                      >
                        {granted}/{requested}
                      </span>
                      <span className="block text-[11px] text-ink-faint">granted</span>
                    </span>
                    <span className="whitespace-nowrap text-xs text-ink-faint">
                      {relativeTime(row.decided_at)}
                    </span>
                  </button>
                </li>
              )
            })}
          </ul>
          <div>
            <DelegationDetail delegation={detail} loading={detailLoading} />
          </div>
        </div>
      )}
    </div>
  )
}
