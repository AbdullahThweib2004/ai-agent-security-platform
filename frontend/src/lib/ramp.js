// The severity rail: a 3px left edge on every table row, coloured by where the
// row sits on the attention ramp. The point is pre-attentive triage — the
// analyst sees which rows matter before reading any of them.
//
// Quiet rows deliberately get a transparent rail rather than a grey one:
// marking everything marks nothing. Only the rows that owe attention carry
// colour, and critical rows additionally take a faint wash of their own hue.
//
// The rail is a mark, not text, so the 3:1 `critical` step is the correct one
// here — `critical-ink` is reserved for red type.

const RAIL = {
  critical: 'border-l-status-critical bg-status-critical/[0.07] hover:bg-status-critical/[0.12]',
  serious: 'border-l-status-serious hover:bg-raised',
  warning: 'border-l-status-warning hover:bg-raised',
  good: 'border-l-transparent hover:bg-raised',
  neutral: 'border-l-transparent hover:bg-raised',
  // What the analyst is doing outranks what the data says, so a selected row
  // shows the accent instead of its severity.
  selected: 'border-l-accent bg-raised',
}

/** Row classes for a ramp step. Pass `selected` to override with the accent. */
export function railClass(step, selected = false) {
  return `border-l-[3px] transition-colors ${selected ? RAIL.selected : RAIL[step] ?? RAIL.neutral}`
}

export default RAIL
