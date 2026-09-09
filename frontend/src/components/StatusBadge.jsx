// Status is never carried by color alone: every badge ships an icon and a word.
const HEALTH = {
  healthy: { label: 'Healthy', icon: '✓', cls: 'text-status-good border-status-good/40 bg-status-good/10' },
  suspicious: { label: 'Suspicious', icon: '▲', cls: 'text-status-critical border-status-critical/40 bg-status-critical/10' },
  // Not a clean bill of health: too little history for any rule to have an
  // opinion yet. Shown distinctly so it is never mistaken for "Healthy".
  unrated: { label: 'Unrated', icon: '?', cls: 'text-status-serious border-status-serious/40 bg-status-serious/10' },
}

const SEVERITY = {
  high: { label: 'High', icon: '▲', cls: 'text-status-critical border-status-critical/40 bg-status-critical/10' },
  medium: { label: 'Medium', icon: '◆', cls: 'text-status-warning border-status-warning/40 bg-status-warning/10' },
  low: { label: 'Low', icon: '●', cls: 'text-status-serious border-status-serious/40 bg-status-serious/10' },
}

const PLATFORM_STATUS = {
  allowed: { label: 'Allowed', icon: '✓', cls: 'text-status-good border-status-good/40 bg-status-good/10' },
  suspicious: { label: 'Suspicious', icon: '▲', cls: 'text-status-critical border-status-critical/40 bg-status-critical/10' },
  blocked: { label: 'Blocked', icon: '⊘', cls: 'text-status-warning border-status-warning/40 bg-status-warning/10' },
}

const SETS = { health: HEALTH, severity: SEVERITY, status: PLATFORM_STATUS }

export default function StatusBadge({ kind = 'health', value, size = 'md' }) {
  const spec = SETS[kind]?.[value] ?? {
    label: value ?? 'Unknown',
    icon: '·',
    cls: 'text-ink-muted border-edge bg-raised',
  }
  const pad = size === 'sm' ? 'px-1.5 py-0.5 text-[11px]' : 'px-2 py-0.5 text-xs'
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border font-medium ${pad} ${spec.cls}`}
    >
      <span aria-hidden="true">{spec.icon}</span>
      {spec.label}
    </span>
  )
}
