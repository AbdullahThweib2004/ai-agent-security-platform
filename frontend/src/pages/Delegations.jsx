import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import DelegationDetail from '../components/DelegationDetail'
import StatTile from '../components/StatTile'
import StatusBadge from '../components/StatusBadge'
import { Empty, ErrorState, Loading } from '../components/States'
import { useFetch } from '../hooks/useFetch'
import { getDelegation, getDelegations } from '../lib/api'
import { relativeTime } from '../lib/format'
import { ClearFilters, FilterBar, SelectFilter, TextFilter } from '../components/Filters'
import { railClass } from '../lib/ramp'
import { rampStep } from '../components/StatusBadge'

// The API's own filter design, mirrored: `decision` is a closed enum, so it is
// a dropdown and an invalid value cannot be typed. Agent ids are open-ended,
// so they stay free text.
const DECISIONS = ['allowed', 'limited', 'blocked']

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

  if (loading && !data) return <Loading label="Loading delegations" rows={6} />
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
        <h1 className="text-title font-semibold">Delegations</h1>
        <p className="mt-1 max-w-[70ch] text-body text-ink-muted">
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

      <FilterBar summary={`${rows.length} shown${restricted > 0 ? ` · ${restricted} restricted` : ''}`}>
        <SelectFilter
          label="Decision"
          value={decision}
          onChange={setDecision}
          options={DECISIONS}
          anyLabel="any decision"
        />
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
        <ClearFilters
          show={Boolean(hasFilters)}
          onClear={() => {
            setDecision('')
            setDelegatorId('')
            setDelegateId('')
          }}
        />
      </FilterBar>

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
              return (
                <li key={row.delegation_id}>
                  <button
                    onClick={() => {
                      setSelectedId(row.delegation_id)
                      navigate(`/delegations/${row.delegation_id}`, { replace: true })
                    }}
                    className={`flex w-full items-center gap-3 px-3 py-2.5 text-left ${railClass(
                      rampStep('delegation', row.decision),
                      active
                    )}`}
                  >
                    <StatusBadge kind="delegation" value={row.decision} size="sm" />
                    <span className="min-w-0 flex-1">
                      <span className="flex flex-wrap items-center gap-x-2 font-mono text-body">
                        <span className="font-medium">{row.delegator_id}</span>
                        <span aria-hidden="true" className="text-ink-faint">→</span>
                        <span className="text-ink-muted">{row.delegate_id}</span>
                      </span>
                      <span className="mt-0.5 block truncate text-label text-ink-muted">
                        {row.reason}
                      </span>
                    </span>
                    {/* At-a-glance signal: how much authority was withheld.
                        Coloured by the decision, not by the count. A reduced
                        permission still counts as granted, so 1/1 in green
                        would claim nothing was cut when the form was
                        downgraded. */}
                    <span className="tabular whitespace-nowrap text-right font-mono text-label">
                      <span
                        className={
                          row.decision === 'allowed'
                            ? 'text-status-good'
                            : row.decision === 'blocked'
                              ? 'text-status-critical-ink'
                              : 'text-status-warning'
                        }
                      >
                        {granted}/{requested}
                      </span>
                      <span className="block font-sans text-micro normal-case tracking-normal text-ink-faint">
                        {row.decision === 'limited' ? 'reduced' : 'granted'}
                      </span>
                    </span>
                    <span className="whitespace-nowrap font-mono text-label text-ink-faint">
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
