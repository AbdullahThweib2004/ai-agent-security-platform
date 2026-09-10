// Status is never carried by colour alone: every badge ships an icon and a
// word. That contract is what makes the ramp legal — good against critical
// measures ΔE 4.1 under deuteranopia, so hue can never be the sole channel.
//
// One ordered ramp answers every "how much should this worry me" question:
//
//   critical  something was refused, or is behaving badly   (red)
//   serious   partially contained / mid severity            (orange)
//   warning   limited, or nobody can vouch for it yet       (yellow)
//   good      allowed, healthy, internal                    (green)
//   neutral   settled — resolved, no opinion                (grey)
//
// Colour is assigned by how much attention the row deserves, never by where it
// sits in a lifecycle. Red text uses the lighter `critical-ink` step, which
// clears 4.5:1 at badge sizes where the mark step does not.
const RAMP = {
  critical: 'text-status-critical-ink border-status-critical/50 bg-status-critical/[0.14]',
  serious: 'text-status-serious border-status-serious/40 bg-status-serious/10',
  warning: 'text-status-warning border-status-warning/40 bg-status-warning/10',
  good: 'text-status-good border-status-good/40 bg-status-good/10',
  neutral: 'text-ink-muted border-edge bg-raised',
  // Trust's "external but vouched for" is a classification, not a severity —
  // the accent keeps it off the ramp entirely.
  accent: 'text-accent border-accent/40 bg-accent/10',
}

const HEALTH = {
  healthy: { label: 'Healthy', icon: '✓', step: 'good' },
  suspicious: { label: 'Suspicious', icon: '▲', step: 'critical' },
  // Not a clean bill of health: too little history for any rule to have an
  // opinion yet. Yellow rather than orange — it separates from critical at
  // ΔE 20.5 normal / 24.4 tritan, where orange managed only 15.7 / 15.0.
  unrated: { label: 'Unrated', icon: '?', step: 'warning' },
}

// Ordered so the hue gets hotter as severity rises. This previously ran
// backwards: `low` was orange and `medium` yellow, so a low-severity alert
// read as more urgent than a medium one.
const SEVERITY = {
  high: { label: 'High', icon: '▲', step: 'critical' },
  medium: { label: 'Medium', icon: '◆', step: 'serious' },
  low: { label: 'Low', icon: '●', step: 'warning' },
}

const PLATFORM_STATUS = {
  allowed: { label: 'Allowed', icon: '✓', step: 'good' },
  suspicious: { label: 'Suspicious', icon: '▲', step: 'critical' },
  // Was yellow here and red in the delegation and interaction sets. The same
  // word means the same thing in all three, so it takes the same colour.
  blocked: { label: 'Blocked', icon: '⊘', step: 'critical' },
}

const DELEGATION = {
  allowed: { label: 'Allowed', icon: '✓', step: 'good' },
  limited: { label: 'Limited', icon: '◆', step: 'warning' },
  blocked: { label: 'Blocked', icon: '⊘', step: 'critical' },
}

const PERMISSION = {
  allowed: { label: 'Allowed', icon: '✓', step: 'good' },
  limited: { label: 'Reduced', icon: '◆', step: 'warning' },
  blocked: { label: 'Blocked', icon: '⊘', step: 'critical' },
}

// Interaction outcomes are binary — there is no partial conversation.
const INTERACTION = {
  allowed: { label: 'Allowed', icon: '✓', step: 'good' },
  blocked: { label: 'Blocked', icon: '⊘', step: 'critical' },
}

const TRUST = {
  internal: { label: 'Internal', icon: '⌂', step: 'good' },
  external_trusted: { label: 'External · trusted', icon: '✓', step: 'accent' },
  external_untrusted: { label: 'External · untrusted', icon: '⊘', step: 'critical' },
  unrated: { label: 'Unrated', icon: '?', step: 'warning' },
}

// A lifecycle, but still coloured by attention owed: an open incident is
// containing an agent right now; contained is mid-way; resolved is settled,
// which is not the same as good.
const INCIDENT = {
  open: { label: 'Open', icon: '⊘', step: 'critical' },
  contained: { label: 'Contained', icon: '◆', step: 'serious' },
  resolved: { label: 'Resolved', icon: '✓', step: 'neutral' },
}

const SETS = {
  incident: INCIDENT,
  health: HEALTH,
  severity: SEVERITY,
  status: PLATFORM_STATUS,
  delegation: DELEGATION,
  permission: PERMISSION,
  interaction: INTERACTION,
  trust: TRUST,
}

/** The ramp step a value sits on, for callers that need to colour something
 *  else to match — a row rail, a chart mark, a canvas node. */
export function rampStep(kind, value) {
  return SETS[kind]?.[value]?.step ?? 'neutral'
}

export default function StatusBadge({ kind = 'health', value, size = 'md' }) {
  const spec = SETS[kind]?.[value]
  const cls = RAMP[spec?.step ?? 'neutral']
  const pad = size === 'sm' ? 'px-1.5 py-0.5 text-micro' : 'px-2 py-0.5 text-label'
  return (
    <span
      className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border font-medium ${pad} ${cls}`}
    >
      <span aria-hidden="true">{spec?.icon ?? '·'}</span>
      {spec?.label ?? value ?? 'Unknown'}
    </span>
  )
}
