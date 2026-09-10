import { compactNumber } from '../lib/format'

/**
 * label (12px) · value (24px display, auto-compact) · optional note (11px).
 * Proportional figures, not tabular — these are standalone display numbers,
 * not a column to be scanned down.
 */
export default function StatTile({ label, value, note, tone = 'neutral', icon }) {
  const toneCls = {
    neutral: 'text-ink',
    good: 'text-status-good',
    warning: 'text-status-warning',
    // The text step, not the mark step: this is 24px but the icon beside it
    // is small, and one red is easier to reason about than two.
    critical: 'text-status-critical-ink',
  }[tone]

  return (
    <div className="rounded-lg border border-edge bg-panel p-4">
      <div className="text-label font-medium text-ink-muted">{label}</div>
      <div className={`mt-1 flex items-baseline gap-2 text-display font-semibold ${toneCls}`}>
        {icon && <span aria-hidden="true" className="text-heading">{icon}</span>}
        {typeof value === 'number' ? compactNumber(value) : value}
      </div>
      {note && <div className="mt-1 text-micro normal-case tracking-normal text-ink-faint">{note}</div>}
    </div>
  )
}
