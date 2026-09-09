import StatusBadge from './StatusBadge'
import { Loading } from './States'
import { ENTITY_ICON, compactNumber, money, shortTime } from '../lib/format'

function Row({ label, children }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1">
      <span className="text-xs text-ink-faint">{label}</span>
      <span className="text-right text-xs text-ink-muted">{children}</span>
    </div>
  )
}

function Chips({ items, empty = 'none' }) {
  if (!items || items.length === 0)
    return <div className="mt-1 text-xs italic text-ink-faint">{empty}</div>
  return (
    <div className="mt-1 flex flex-wrap gap-1">
      {items.map((i) => (
        <span key={i} className="rounded bg-raised px-1.5 py-0.5 font-mono text-[11px] text-ink-muted">
          {i}
        </span>
      ))}
    </div>
  )
}

/** Side panel for the selected node: identity, counts, and its baseline. */
export default function EntityDetail({ node, detail, loading }) {
  if (!node)
    return (
      <div className="rounded-lg border border-dashed border-edge bg-panel p-6 text-center text-xs text-ink-faint">
        Select a node to inspect it.
      </div>
    )

  const b = detail?.baseline
  const range = b?.typical_value_range

  return (
    <div className="space-y-3 rounded-lg border border-edge bg-panel p-4">
      <div>
        <div className="flex items-center gap-2">
          <span aria-hidden="true" className="text-ink-faint">{ENTITY_ICON[node.type] ?? '·'}</span>
          <span className="text-sm font-semibold">{node.id}</span>
        </div>
        <div className="mt-2 flex items-center gap-2">
          <StatusBadge kind="health" value={node.health} size="sm" />
          <span className="text-xs text-ink-faint">{node.type}</span>
        </div>
      </div>

      <div className="border-t border-edge pt-2">
        <Row label="Actions initiated">{compactNumber(node.event_count)}</Row>
        <Row label="Actions received">{compactNumber(node.inbound_count)}</Row>
        <Row label="Alerts raised">
          {node.alert_count > 0 ? (
            <span className="text-status-critical">{node.alert_count}</span>
          ) : (
            '0'
          )}
        </Row>
        <Row label="First seen">{shortTime(node.first_seen)}</Row>
        <Row label="Last seen">{shortTime(node.last_seen)}</Row>
      </div>

      {node.type === 'agent' && (
        <div className="border-t border-edge pt-2">
          <div className="mb-1 text-xs font-semibold text-ink-muted">Baseline</div>
          {loading ? (
            <Loading label="Computing baseline" />
          ) : !b ? (
            <span className="text-xs italic text-ink-faint">unavailable</span>
          ) : !b.is_established ? (
            <p className="text-xs text-status-serious">
              Not established — only {b.event_count} clean event
              {b.event_count === 1 ? '' : 's'} on record, so no rule has judged
              this agent yet.
            </p>
          ) : (
            <div className="space-y-2">
              <Row label="Clean events">{b.event_count}</Row>
              <div>
                <span className="text-xs text-ink-faint">Usual agents contacted</span>
                <Chips items={b.usual_agents_contacted} />
              </div>
              <div>
                <span className="text-xs text-ink-faint">Usual tools &amp; APIs</span>
                <Chips items={[...b.usual_tools, ...b.usual_apis, ...b.usual_databases]} />
              </div>
              <div>
                <span className="text-xs text-ink-faint">Usual permissions</span>
                <Chips items={b.usual_permissions} />
              </div>
              {range?.samples > 0 && (
                <div>
                  <span className="text-xs text-ink-faint">Typical value range</span>
                  <div className="mt-1 text-xs text-ink-muted">
                    {money(range.min)} – {money(range.max)}{' '}
                    <span className="text-ink-faint">
                      (mean {money(range.mean)}, {range.samples} samples)
                    </span>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
