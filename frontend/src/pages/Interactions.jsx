import { useEffect, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import InteractionDetail from '../components/InteractionDetail'
import StatTile from '../components/StatTile'
import StatusBadge from '../components/StatusBadge'
import { Empty, ErrorState, Loading } from '../components/States'
import { useFetch } from '../hooks/useFetch'
import { getA2ADecision, getA2ADecisions } from '../lib/api'
import { relativeTime } from '../lib/format'

// Mirrors the API's own filter design: `decision` is a closed set, so it is a
// dropdown; agent ids are open-ended, so they stay free text.
const DECISIONS = ['allowed', 'blocked']

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

function TextFilter({ label, value, onChange }) {
  return (
    <label className="flex items-center gap-2 text-xs text-ink-faint">
      {label}
      <input
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="any agent"
        className="w-40 rounded border border-edge bg-raised px-2 py-1 text-xs text-ink placeholder:text-ink-faint focus:border-accent focus:outline-none"
      />
    </label>
  )
}

export default function Interactions() {
  const { decisionId } = useParams()
  const navigate = useNavigate()
  const [decision, setDecision] = useState('')
  const [requesterId, setRequesterId] = useState('')
  const [targetId, setTargetId] = useState('')
  const [selectedId, setSelectedId] = useState(decisionId ?? null)

  // Filters are query parameters, so every change is a fresh request. Filtering
  // in memory would quietly lie once the result set exceeds the page limit.
  const { data, error, loading, reload } = useFetch(
    () =>
      getA2ADecisions({
        decision,
        requester_id: requesterId.trim(),
        target_id: targetId.trim(),
        limit: 200,
      }),
    [decision, requesterId, targetId]
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
    getA2ADecision(selectedId)
      .then((d) => !cancelled && setDetail(d))
      .catch(() => !cancelled && setDetail(null))
      .finally(() => !cancelled && setDetailLoading(false))
    return () => {
      cancelled = true
    }
  }, [selectedId])

  useEffect(() => {
    if (!data || data.length === 0) return
    if (!selectedId || !data.some((d) => d.decision_id === selectedId)) {
      setSelectedId(data[0].decision_id)
    }
  }, [data, selectedId])

  if (loading && !data) return <Loading label="Loading interactions" />
  if (error) return <ErrorState message={error} onRetry={reload} />

  const rows = data ?? []
  const blocked = rows.filter((r) => r.decision === 'blocked').length
  const untrusted = rows.filter((r) => r.rule === 'untrusted_party').length
  const unrated = rows.filter((r) => r.rule === 'unrated_counterparty').length
  const hasFilters = decision || requesterId || targetId

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-lg font-semibold">Interactions</h1>
        <p className="mt-0.5 text-sm text-ink-muted">
          Whether two agents should be talking at all — judged on identity and
          trust, before any question of what authority travels between them.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile label="Interactions" value={rows.length} />
        <StatTile
          label="Refused"
          value={blocked}
          tone={blocked ? 'critical' : 'good'}
          icon={blocked ? '⊘' : '✓'}
          note="conversation declined"
        />
        <StatTile
          label="Untrusted party"
          value={untrusted}
          tone={untrusted ? 'critical' : 'neutral'}
          note="explicitly classified"
        />
        <StatTile
          label="Unvouched counterparty"
          value={unrated}
          tone={unrated ? 'warning' : 'neutral'}
          note="too little history"
        />
      </div>

      <div className="flex flex-wrap items-center gap-4 rounded-lg border border-edge bg-panel px-3 py-2">
        <DecisionFilter value={decision} onChange={setDecision} />
        <TextFilter label="Requester" value={requesterId} onChange={setRequesterId} />
        <TextFilter label="Target" value={targetId} onChange={setTargetId} />
        {hasFilters && (
          <button
            onClick={() => {
              setDecision('')
              setRequesterId('')
              setTargetId('')
            }}
            className="text-xs text-accent hover:underline"
          >
            clear filters
          </button>
        )}
        <span className="ml-auto text-xs text-ink-faint">
          {rows.length} shown{blocked > 0 && ` · ${blocked} refused`}
        </span>
      </div>

      {rows.length === 0 ? (
        <Empty
          title="No interactions match"
          hint={
            hasFilters
              ? 'Try clearing the filters.'
              : 'Seed the platform: docker compose exec backend python scripts/simulate.py --reset'
          }
        />
      ) : (
        <div className="grid items-start gap-4 lg:grid-cols-[1fr_23rem]">
          <ul className="divide-y divide-edge overflow-hidden rounded-lg border border-edge">
            {rows.map((row) => {
              const active = row.decision_id === selectedId
              return (
                <li key={row.decision_id}>
                  <button
                    onClick={() => {
                      setSelectedId(row.decision_id)
                      navigate(`/interactions/${row.decision_id}`, { replace: true })
                    }}
                    className={`flex w-full items-center gap-3 px-4 py-3 text-left transition-colors ${
                      active ? 'bg-raised' : 'bg-surface hover:bg-panel/70'
                    }`}
                  >
                    <StatusBadge kind="interaction" value={row.decision} size="sm" />
                    <span className="min-w-0 flex-1">
                      <span className="flex flex-wrap items-center gap-x-2 text-sm">
                        <span className="font-medium">{row.requester_id}</span>
                        <span className="text-ink-faint">→</span>
                        <span className="text-ink-muted">{row.target_id}</span>
                      </span>
                      <span className="mt-0.5 block truncate text-xs text-ink-muted">
                        {row.reason}
                      </span>
                    </span>
                    {/* The trust that produced the verdict, at a glance. */}
                    <span className="hidden shrink-0 items-center gap-1 xl:flex">
                      <StatusBadge kind="trust" value={row.requester_trust} size="sm" />
                      <span className="text-ink-faint">→</span>
                      <StatusBadge kind="trust" value={row.target_trust} size="sm" />
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
            <InteractionDetail decision={detail} loading={detailLoading} />
          </div>
        </div>
      )}
    </div>
  )
}
