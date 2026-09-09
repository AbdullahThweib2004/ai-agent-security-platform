import StatusBadge from './StatusBadge'
import { Loading } from './States'
import { shortTime } from '../lib/format'

const RULE_LABEL = {
  untrusted_party: 'A party is classified untrusted',
  unrated_counterparty: 'Counterparty cannot be vouched for',
  default_allow: 'Both parties permitted to interact',
}

/** One interaction verdict, with the trust it was decided under. */
export default function InteractionDetail({ decision, loading }) {
  if (loading) return <Loading label="Loading decision" />
  if (!decision)
    return (
      <div className="rounded-lg border border-dashed border-edge bg-panel p-6 text-center text-xs text-ink-faint">
        Select an interaction to see why it was permitted or refused.
      </div>
    )

  return (
    <div className="space-y-4 rounded-lg border border-edge bg-panel p-4">
      <div>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2 text-sm font-medium">
              <span>{decision.requester_id}</span>
              <span className="text-ink-faint">→</span>
              <span>{decision.target_id}</span>
            </div>
            <div className="mt-1 text-[11px] text-ink-faint">
              decided {shortTime(decision.decided_at)}
            </div>
          </div>
          <StatusBadge kind="interaction" value={decision.decision} size="sm" />
        </div>
      </div>

      <div className="border-t border-edge pt-3">
        <div className="mb-2 text-xs font-semibold text-ink-muted">
          Trust at decision time
        </div>
        {/* Snapshotted, not looked up live: the identity rows are mutable and
            this record is not, so a later reclassification cannot rewrite why
            something was decided. */}
        <div className="space-y-2">
          <div className="flex items-center justify-between gap-3">
            <span className="truncate text-xs text-ink-muted">{decision.requester_id}</span>
            <StatusBadge kind="trust" value={decision.requester_trust} size="sm" />
          </div>
          <div className="flex items-center justify-between gap-3">
            <span className="truncate text-xs text-ink-muted">{decision.target_id}</span>
            <StatusBadge kind="trust" value={decision.target_trust} size="sm" />
          </div>
        </div>
        <p className="mt-2 text-[11px] italic text-ink-faint">
          As recorded when the verdict was reached, not as they stand now.
        </p>
      </div>

      <div className="border-t border-edge pt-3">
        <div className="mb-1 text-xs font-semibold text-ink-muted">Rule</div>
        <div className="text-xs text-ink">{RULE_LABEL[decision.rule] ?? decision.rule}</div>
        <div className="mt-0.5 font-mono text-[11px] text-ink-faint">{decision.rule}</div>
        <p className="mt-2 text-xs leading-relaxed text-ink-muted">{decision.reason}</p>
      </div>

      <div className="border-t border-edge pt-3">
        <div className="mb-1 text-xs text-ink-faint">Event</div>
        <code className="break-all font-mono text-[11px] text-ink-muted">
          {decision.event_id}
        </code>
      </div>
    </div>
  )
}
