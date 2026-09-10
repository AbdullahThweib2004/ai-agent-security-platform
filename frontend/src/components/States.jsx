export function Loading({ label = 'Loading', rows = 5 }) {
  // Skeleton rows match real table geometry so arriving data causes no layout
  // shift — the old pulsing dot collapsed to nothing and moved the page.
  return (
    <div className="overflow-hidden rounded-lg border border-edge" role="status" aria-live="polite">
      <div className="border-b border-edge bg-panel px-3 py-2 text-micro uppercase text-ink-faint">
        {label}…
      </div>
      <div className="divide-y divide-edge bg-surface">
        {Array.from({ length: rows }, (_, i) => (
          <div key={i} className="flex items-center gap-3 px-3 py-2.5">
            <div className="h-2.5 flex-1 animate-pulse rounded bg-raised" style={{ maxWidth: `${52 - i * 6}%` }} />
            <div className="h-2.5 w-16 animate-pulse rounded bg-raised" />
            <div className="h-2.5 w-10 animate-pulse rounded bg-raised" />
          </div>
        ))}
      </div>
    </div>
  )
}

export function ErrorState({ message, onRetry }) {
  return (
    <div className="rounded-lg border border-status-critical/40 bg-status-critical/10 p-4">
      <div className="flex items-center gap-2 text-body font-medium text-status-critical-ink">
        <span aria-hidden="true">▲</span> Could not load data
      </div>
      <p className="mt-1 font-mono text-label text-ink-muted">{message}</p>
      <p className="mt-2 text-label text-ink-faint">
        Is the backend running on <code className="font-mono">localhost:8000</code>?
      </p>
      {onRetry && (
        <button
          onClick={onRetry}
          className="mt-3 rounded border border-edge bg-raised px-3 py-1 text-label text-ink transition-colors hover:border-accent"
        >
          Retry
        </button>
      )}
    </div>
  )
}

export function Empty({ title, hint }) {
  return (
    <div className="rounded-lg border border-dashed border-edge bg-panel px-4 py-10 text-center">
      <div className="text-body text-ink-muted">{title}</div>
      {hint && <div className="mt-1 font-mono text-label text-ink-faint">{hint}</div>}
    </div>
  )
}
