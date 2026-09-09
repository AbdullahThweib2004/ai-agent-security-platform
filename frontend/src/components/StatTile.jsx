import { compactNumber } from '../lib/format'

/**
 * label (sentence case) · value (semibold, auto-compact) · optional note.
 * Proportional figures, not tabular — these are standalone display numbers.
 */
export default function StatTile({ label, value, note, tone = 'neutral', icon }) {
  const toneCls = {
    neutral: 'text-ink',
    good: 'text-status-good',
    warning: 'text-status-warning',
    critical: 'text-status-critical',
  }[tone]

  return (
    <div className="rounded-lg border border-edge bg-panel px-4 py-3">
      <div className="text-xs font-medium text-ink-muted">{label}</div>
      <div className={`mt-1 flex items-baseline gap-2 text-2xl font-semibold ${toneCls}`}>
        {icon && <span aria-hidden="true" className="text-base">{icon}</span>}
        {typeof value === 'number' ? compactNumber(value) : value}
      </div>
      {note && <div className="mt-0.5 text-xs text-ink-faint">{note}</div>}
    </div>
  )
}
