import StatusBadge from './StatusBadge'
import { Loading } from './States'
import { shortTime } from '../lib/format'

const RULE_LABEL = {
  confinement: 'Delegator does not hold it',
  sensitive_category: 'Sensitive category',
  unrated_delegate: 'Delegate is unrated',
  default_allow: 'Held and unrestricted',
}

/** The permission-by-permission breakdown behind one decision. */
export default function DelegationDetail({ delegation, loading }) {
  if (loading) return <Loading label="Loading decision" />
  if (!delegation)
    return (
      <div className="rounded-lg border border-dashed border-edge bg-panel p-6 text-center text-xs text-ink-faint">
        Select a delegation to see what was cut and why.
      </div>
    )

  const { permission_decisions: verdicts = [] } = delegation
  const granted = delegation.granted_permissions ?? []
  const requested = delegation.requested_permissions ?? []

  return (
    <div className="space-y-4 rounded-lg border border-edge bg-panel p-4">
      <div>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2 text-sm font-medium">
              <span>{delegation.delegator_id}</span>
              <span className="text-ink-faint">→</span>
              <span>{delegation.delegate_id}</span>
            </div>
            <div className="mt-1 text-[11px] text-ink-faint">
              decided {shortTime(delegation.decided_at)}
            </div>
          </div>
          <StatusBadge kind="delegation" value={delegation.decision} size="sm" />
        </div>
        <p className="mt-2 text-xs leading-relaxed text-ink-muted">{delegation.reason}</p>
      </div>

      <div className="grid grid-cols-2 gap-3 border-t border-edge pt-3">
        <div>
          <div className="text-xs text-ink-faint">Requested</div>
          <div className="tabular mt-0.5 text-lg font-semibold">{requested.length}</div>
        </div>
        <div>
          <div className="text-xs text-ink-faint">Granted</div>
          <div
            className={`tabular mt-0.5 text-lg font-semibold ${
              granted.length === 0 && requested.length > 0
                ? 'text-status-critical'
                : granted.length < requested.length
                  ? 'text-status-warning'
                  : 'text-status-good'
            }`}
          >
            {granted.length}
          </div>
        </div>
      </div>

      <div className="border-t border-edge pt-3">
        <div className="mb-2 text-xs font-semibold text-ink-muted">
          Permission by permission
        </div>
        {verdicts.length === 0 ? (
          <p className="text-xs italic text-ink-faint">
            No permissions were requested, so nothing was delegated.
          </p>
        ) : (
          <ul className="space-y-2">
            {verdicts.map((v) => (
              <li key={v.permission} className="rounded border border-edge bg-surface p-2.5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <code className="font-mono text-[11px] text-ink">{v.permission}</code>
                  <StatusBadge kind="permission" value={v.decision} size="sm" />
                </div>
                {v.granted_as && v.granted_as !== v.permission && (
                  <div className="mt-1 flex items-center gap-1.5 text-[11px]">
                    <span className="text-ink-faint">granted as</span>
                    <code className="rounded bg-raised px-1.5 py-0.5 font-mono text-status-warning">
                      {v.granted_as}
                    </code>
                  </div>
                )}
                <div className="mt-1.5 text-[11px] text-ink-faint">
                  {RULE_LABEL[v.rule] ?? v.rule}
                  <span className="mx-1">·</span>
                  <span className="font-mono">{v.rule}</span>
                </div>
                <p className="mt-1 text-[11px] leading-relaxed text-ink-muted">{v.reason}</p>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
