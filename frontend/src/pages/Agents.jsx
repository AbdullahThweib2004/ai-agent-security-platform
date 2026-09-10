import { Link } from 'react-router-dom'
import StatTile from '../components/StatTile'
import StatusBadge from '../components/StatusBadge'
import { Empty, ErrorState, Loading } from '../components/States'
import { useFetch } from '../hooks/useFetch'
import { getGraph, getIncidents } from '../lib/api'
import { ENTITY_ICON, compactNumber, relativeTime } from '../lib/format'
import { railClass } from '../lib/ramp'

export default function Agents() {
  const { data, error, loading, reload } = useFetch(getGraph, [])
  // The graph knows behaviour; it does not know containment. Merging the two
  // here is what lets this page say "suspended" rather than only "suspicious".
  const { data: incidents } = useFetch(() => getIncidents({ limit: 200 }), [])

  if (loading) return <Loading label="Loading agents" />
  if (error) return <ErrorState message={error} onRetry={reload} />

  const nodes = data?.nodes ?? []
  const containedBy = Object.fromEntries(
    (incidents ?? []).filter((i) => i.is_suspended).map((i) => [i.agent_id, i])
  )
  const agents = nodes
    .filter((n) => n.type === 'agent')
    .sort((a, b) => {
      // Anything needing attention sorts to the top: confirmed problems first,
      // then agents nobody can vouch for yet, then the quiet ones.
      const rank = { suspicious: 0, unrated: 1, healthy: 2 }
      const byHealth = (rank[a.health] ?? 3) - (rank[b.health] ?? 3)
      if (byHealth !== 0) return byHealth
      return b.alert_count - a.alert_count || b.event_count - a.event_count
    })
  const others = nodes.filter((n) => n.type !== 'agent')
  const stats = data?.stats ?? {}
  const totalAlerts = nodes.reduce((sum, n) => sum + n.alert_count, 0)
  const totalActions = nodes.reduce((sum, n) => sum + n.event_count, 0)

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-title font-semibold">Agents</h1>
        <p className="mt-1 max-w-[70ch] text-body text-ink-muted">
          Every autonomous agent observed, graded on this platform's own verdict —
          not on what the agent reported about itself.
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile
          label="Agents monitored"
          value={agents.length}
          note={
            stats.unrated_node_count
              ? `${stats.unrated_node_count} with too little history to judge`
              : 'all have an established baseline'
          }
        />
        <StatTile
          label="Suspicious agents"
          value={stats.suspicious_node_count ?? 0}
          tone={stats.suspicious_node_count ? 'critical' : 'good'}
          icon={stats.suspicious_node_count ? '▲' : '✓'}
          note={stats.suspicious_node_count ? 'needs review' : 'all clear'}
        />
        <StatTile
          label="Agents contained"
          value={Object.keys(containedBy).length}
          tone={Object.keys(containedBy).length ? 'critical' : 'good'}
          icon={Object.keys(containedBy).length ? '⊘' : '✓'}
          note={
            Object.keys(containedBy).length
              ? 'suspended — activity still recorded'
              : 'none suspended'
          }
        />
        <StatTile label="Actions observed" value={totalActions} note={`${stats.edge_count ?? 0} relationships`} />
      </div>

      {agents.length === 0 ? (
        <Empty
          title="No agents observed yet"
          hint="Seed the platform: docker compose exec backend python scripts/simulate.py --reset"
        />
      ) : (
        <div className="overflow-hidden rounded-lg border border-edge">
          <table className="w-full text-body">
            <thead className="bg-panel text-left text-micro uppercase text-ink-faint">
              <tr>
                <th className="px-3 py-2 font-semibold">Agent</th>
                <th className="px-3 py-2 font-semibold">Status</th>
                <th className="px-3 py-2 text-right font-semibold">Alerts</th>
                <th className="px-3 py-2 text-right font-semibold">Initiated</th>
                <th className="px-3 py-2 text-right font-semibold">Received</th>
                <th className="px-3 py-2 font-semibold">Last seen</th>
                <th className="px-3 py-2" />
              </tr>
            </thead>
            <tbody className="divide-y divide-edge bg-surface">
              {agents.map((a) => (
                <tr
                  key={a.id}
                  className={railClass(
                    a.health === 'suspicious' ? 'critical' : a.health === 'unrated' ? 'warning' : 'good'
                  )}
                >
                  <td className="px-3 py-2.5">
                    <div className="flex items-center gap-2">
                      <span aria-hidden="true" className="text-ink-faint">
                        {ENTITY_ICON.agent}
                      </span>
                      <span className="font-mono font-medium">{a.id}</span>
                    </div>
                  </td>
                  <td className="px-3 py-2.5">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <StatusBadge kind="health" value={a.health} />
                      {containedBy[a.id] && (
                        <Link
                          to={`/incidents/${containedBy[a.id].incident_id}`}
                          title="Contained: its events are still recorded, but marked"
                        >
                          <StatusBadge kind="incident" value="open" size="sm" />
                        </Link>
                      )}
                    </div>
                  </td>
                  <td className="tabular px-3 py-2.5 text-right font-mono">
                    {a.alert_count > 0 ? (
                      <span className="font-medium text-status-critical-ink">{a.alert_count}</span>
                    ) : (
                      <span className="text-ink-faint">0</span>
                    )}
                  </td>
                  <td className="tabular px-3 py-2.5 text-right font-mono text-ink-muted">
                    {compactNumber(a.event_count)}
                  </td>
                  <td className="tabular px-3 py-2.5 text-right font-mono text-ink-muted">
                    {compactNumber(a.inbound_count)}
                  </td>
                  <td className="px-3 py-2.5 font-mono text-label text-ink-muted">
                    {relativeTime(a.last_seen)}
                    {a.health === 'unrated' && (
                      <div className="text-micro normal-case tracking-normal text-status-warning">
                        only {a.clean_event_count} clean event
                        {a.clean_event_count === 1 ? '' : 's'}
                      </div>
                    )}
                  </td>
                  <td className="px-3 py-2.5 text-right">
                    <Link
                      to={`/graph/${encodeURIComponent(a.id)}`}
                      className="rounded border border-edge px-2 py-1 text-label text-ink-muted transition-colors hover:border-accent hover:text-ink"
                    >
                      Inspect
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {others.length > 0 && (
        <div>
          <h2 className="text-heading font-semibold text-ink-muted">Other entities</h2>
          <div className="mt-2 flex flex-wrap gap-2">
            {others.map((n) => (
              <span
                key={n.id}
                className="inline-flex items-center gap-2 rounded-md border border-edge bg-panel px-2.5 py-1.5 text-label"
                title={`${n.event_count} initiated · ${n.inbound_count} received`}
              >
                <span aria-hidden="true" className="text-ink-faint">{ENTITY_ICON[n.type] ?? '·'}</span>
                <span className="font-mono">{n.id}</span>
                <span className="text-micro uppercase text-ink-faint">{n.type}</span>
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
