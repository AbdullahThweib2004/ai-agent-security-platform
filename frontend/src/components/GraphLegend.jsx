import { HEALTH_COLOR, TYPE_COLOR } from '../lib/palette'
import { ENTITY_ICON } from '../lib/format'

export default function GraphLegend() {
  return (
    <div className="flex flex-wrap items-center gap-x-5 gap-y-2 rounded-lg border border-edge bg-panel px-3 py-2 text-xs">
      <span className="font-medium text-ink-faint">Entity</span>
      {Object.entries(TYPE_COLOR).map(([type, color]) => (
        <span key={type} className="flex items-center gap-1.5 text-ink-muted">
          <span aria-hidden="true" style={{ color }}>{ENTITY_ICON[type]}</span>
          {type}
        </span>
      ))}
      <span className="ml-2 font-medium text-ink-faint">Ring</span>
      {Object.entries(HEALTH_COLOR).map(([health, color]) => (
        <span key={health} className="flex items-center gap-1.5 text-ink-muted">
          <span
            aria-hidden="true"
            className="inline-block h-2.5 w-2.5 rounded-full border-2"
            style={{ borderColor: color }}
          />
          {health}
        </span>
      ))}
      <span className="ml-2 flex items-center gap-1.5 text-ink-muted">
        <span aria-hidden="true" className="inline-block h-0.5 w-6" style={{ background: HEALTH_COLOR.suspicious }} />
        suspicious relationship
      </span>
    </div>
  )
}
