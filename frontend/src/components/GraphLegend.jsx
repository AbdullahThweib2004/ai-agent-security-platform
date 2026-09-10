import { healthColor, TYPE_SHAPE } from '../lib/palette'
import { ENTITY_ICON } from '../lib/format'

// Two channels, taught separately: shape says what an entity is, colour says
// how much it should worry you. Keeping them in one row would imply they are
// one vocabulary.
const HEALTH = [
  { key: 'healthy', label: 'healthy' },
  { key: 'unrated', label: 'unrated' },
  { key: 'suspicious', label: 'suspicious' },
]

export default function GraphLegend() {
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-2 rounded-lg border border-edge bg-panel px-3 py-2 text-label">
      <span className="text-micro uppercase text-ink-faint">Shape · type</span>
      {Object.keys(TYPE_SHAPE).map((type) => (
        <span key={type} className="flex items-center gap-1.5 text-ink-muted">
          <span aria-hidden="true" className="text-ink-faint">{ENTITY_ICON[type]}</span>
          {type}
        </span>
      ))}

      <span className="ml-2 text-micro uppercase text-ink-faint">Colour · health</span>
      {HEALTH.map(({ key, label }) => (
        <span key={key} className="flex items-center gap-1.5 text-ink-muted">
          <span
            aria-hidden="true"
            className="inline-block h-2.5 w-2.5 rounded-full"
            style={{ background: healthColor(key) }}
          />
          {label}
        </span>
      ))}

      <span className="ml-2 flex items-center gap-1.5 text-ink-muted">
        <span aria-hidden="true" className="inline-block h-0.5 w-6" style={{ background: healthColor('suspicious') }} />
        suspicious relationship
      </span>
    </div>
  )
}
