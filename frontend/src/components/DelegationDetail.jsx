import { Link } from 'react-router-dom'
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
  if (loading) return <Loading label="Loading decision" rows={3} />
  if (!delegation)
    return (
      <div className="rounded-lg border border-dashed border-edge bg-panel p-6 text-center text-label text-ink-faint">
        Select a delegation to see what was cut and why.
      </div>
    )

  const { permission_decisions: verdicts = [] } = delegation
  // A delegation refused before it was evaluated cites the interaction decision
  // by id in its reason. Surface that as a link when present.
  const upstreamId = delegation.reason?.includes('refused upstream')
    ? delegation.reason.match(
        /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i
      )?.[0]
    : null
  const granted = delegation.granted_permissions ?? []
  const requested = delegation.requested_permissions ?? []

  return (
    <div className="space-y-4 rounded-lg border border-edge bg-panel p-4">
      <div>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2 font-mono text-body font-medium">
              <span>{delegation.delegator_id}</span>
              <span aria-hidden="true" className="text-ink-faint">→</span>
              <span>{delegation.delegate_id}</span>
            </div>
            <div className="mt-1 font-mono text-micro normal-case tracking-normal text-ink-faint">
              decided {shortTime(delegation.decided_at)}
            </div>
          </div>
          <StatusBadge kind="delegation" value={delegation.decision} size="sm" />
        </div>
        <p className="mt-2 text-label leading-relaxed text-ink-muted">{delegation.reason}</p>
        {upstreamId && (
          // The reason already names the interaction decision textually; this
          // makes it navigable, so "refused upstream" is not a dead end for
          // whoever is reading. The id is parsed out of the reason rather than
          // carried as a field — see the note in the README about promoting it
          // to a real foreign key.
          <Link
            to={`/interactions/${upstreamId}`}
            className="mt-2 inline-flex items-center gap-1.5 rounded border border-edge bg-raised px-2 py-1 text-micro normal-case tracking-normal text-ink-muted transition-colors hover:border-accent hover:text-ink"
          >
            <span aria-hidden="true">⊘</span>
            View the interaction decision that refused this →
          </Link>
        )}
      </div>

      <div className="grid grid-cols-2 gap-3 border-t border-edge pt-3">
        <div>
          <div className="text-label text-ink-faint">Requested</div>
          <div className="mt-0.5 text-title font-semibold">{requested.length}</div>
        </div>
        <div>
          <div className="text-label text-ink-faint">Granted</div>
          <div
            className={`mt-0.5 text-title font-semibold ${
              granted.length === 0 && requested.length > 0
                ? 'text-status-critical-ink'
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
        <div className="mb-2 text-heading font-semibold text-ink-muted">
          Permission by permission
        </div>
        {verdicts.length === 0 ? (
          <p className="text-label italic text-ink-faint">
            No permissions were requested, so nothing was delegated.
          </p>
        ) : (
          <ul className="space-y-2">
            {verdicts.map((v) => (
              <li key={v.permission} className="rounded border border-edge bg-surface p-2.5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <code className="font-mono text-label text-ink">{v.permission}</code>
                  <StatusBadge kind="permission" value={v.decision} size="sm" />
                </div>
                {v.granted_as && v.granted_as !== v.permission && (
                  <div className="mt-1 flex items-center gap-1.5 text-micro normal-case tracking-normal">
                    <span className="text-ink-faint">granted as</span>
                    <code className="rounded bg-raised px-1.5 py-0.5 font-mono text-status-warning">
                      {v.granted_as}
                    </code>
                  </div>
                )}
                <div className="mt-1.5 text-micro normal-case tracking-normal text-ink-faint">
                  {RULE_LABEL[v.rule] ?? v.rule}
                  <span className="mx-1">·</span>
                  <span className="font-mono">{v.rule}</span>
                </div>
                <p className="mt-1 text-micro normal-case tracking-normal leading-relaxed text-ink-muted">{v.reason}</p>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}
