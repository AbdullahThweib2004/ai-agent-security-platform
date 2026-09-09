// Categorical hues, assigned by entity type in fixed order and never cycled.
// Stepped for the dark console surface.
export const TYPE_COLOR = {
  agent: '#3987e5',
  user: '#9085e9',
  tool: '#199e70',
  api: '#d95926',
  database: '#c98500',
}

// Status palette — reserved, never reused as a categorical hue. Always paired
// with a glyph or label so hue is not the only channel.
export const STATUS_COLOR = {
  good: '#0ca30c',
  warning: '#fab219',
  serious: '#ec835a',
  critical: '#d03b3b',
}

export const HEALTH_COLOR = {
  healthy: STATUS_COLOR.good,
  unrated: STATUS_COLOR.serious,
  suspicious: STATUS_COLOR.critical,
}

export const SURFACE = '#12151c'
export const EDGE_IDLE = '#3b465e'
