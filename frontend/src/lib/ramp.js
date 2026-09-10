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

// The resting look, without any interaction state.
const TONE = {
  critical: 'border-l-status-critical bg-status-critical/[0.07]',
  serious: 'border-l-status-serious',
  warning: 'border-l-status-warning',
  good: 'border-l-transparent',
  neutral: 'border-l-transparent',
}

const HOVER = {
  critical: 'hover:bg-status-critical/[0.12]',
  serious: 'hover:bg-raised',
  warning: 'hover:bg-raised',
  good: 'hover:bg-raised',
  neutral: 'hover:bg-raised',
}

// What the analyst is doing outranks what the data says, so a selected row
// shows the accent instead of its severity.
const SELECTED = 'border-l-accent bg-raised'

/** Row classes for a ramp step. Pass `selected` to override with the accent.
 *  For rows the analyst can click — the default across the list views. */
export function railClass(step, selected = false) {
  const key = step in TONE ? step : 'neutral'
  const rest = selected ? SELECTED : `${TONE[key]} ${HOVER[key]}`
  return `border-l-[3px] transition-colors ${rest}`
}

/** The same rail without a hover state, for rows that are not interactive —
 *  a verdict being reported rather than a row to open. */
export function railTone(step) {
  return `border-l-[3px] ${TONE[step in TONE ? step : 'neutral']}`
}

export default TONE
