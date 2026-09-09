export function Loading({ label = 'Loading' }) {
  return (
    <div className="flex items-center gap-3 rounded-lg border border-edge bg-panel px-4 py-8 text-sm text-ink-muted">
      <span className="h-3 w-3 animate-pulse rounded-full bg-accent" />
      {label}…
    </div>
  )
}

export function ErrorState({ message, onRetry }) {
  return (
    <div className="rounded-lg border border-status-critical/40 bg-status-critical/10 px-4 py-6">
      <div className="flex items-center gap-2 text-sm font-medium text-status-critical">
        <span aria-hidden="true">▲</span> Could not load data
      </div>
      <p className="mt-1 font-mono text-xs text-ink-muted">{message}</p>
      <p className="mt-2 text-xs text-ink-faint">
        Is the backend running on <code>localhost:8000</code>?
      </p>
      {onRetry && (
        <button
          onClick={onRetry}
          className="mt-3 rounded border border-edge bg-raised px-3 py-1 text-xs text-ink hover:border-accent"
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
      <div className="text-sm text-ink-muted">{title}</div>
      {hint && <div className="mt-1 text-xs text-ink-faint">{hint}</div>}
    </div>
  )
}
