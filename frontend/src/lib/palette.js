// The canvas cannot use Tailwind classes, but it must not become a second
// source of colour truth either. These read the same custom properties defined
// in index.css, so the palette has exactly one definition.
//
// Reads are lazy and memoised: module evaluation can run before the stylesheet
// is applied, but the first canvas paint cannot.
const cache = new Map()

function token(name) {
  if (!cache.has(name)) {
    const raw = getComputedStyle(document.documentElement)
      .getPropertyValue(`--${name}`)
      .trim()
    // An empty value means the stylesheet has not landed yet; don't memoise a
    // blank, or every later frame inherits it.
    if (!raw) return 'rgb(135 145 173)'
    cache.set(name, `rgb(${raw})`)
  }
  return cache.get(name)
}

export const status = {
  get good() { return token('status-good') },
  get warning() { return token('status-warning') },
  get serious() { return token('status-serious') },
  get critical() { return token('status-critical') },
}

// Health is the graph's colour channel: entity type is carried by node shape,
// so hue is free to say the one thing an analyst is scanning for. Healthy is
// deliberately recessive — marking everything marks nothing.
export const HEALTH_COLOR = {
  healthy: () => token('ink-faint'),
  unrated: () => token('status-warning'),
  suspicious: () => token('status-critical'),
}

export const healthColor = (health) => (HEALTH_COLOR[health] ?? HEALTH_COLOR.healthy)()

export const surface = () => token('surface')
export const panel = () => token('panel')
export const edgeIdle = () => token('edge-strong')
export const ink = () => token('ink')
export const inkMuted = () => token('ink-muted')
export const inkFaint = () => token('ink-faint')
export const accent = () => token('accent')

// Entity type is drawn, not tinted. These are the polygon side counts the
// canvas renders, matching the glyphs ENTITY_ICON uses everywhere else:
// agent ◆ 4, user ● circle, tool ▲ 3, api ■ 4 (square), database ⬢ 6.
export const TYPE_SHAPE = {
  agent: 'diamond',
  user: 'circle',
  tool: 'triangle',
  api: 'square',
  database: 'hexagon',
}

// Optical sizing, not semantic weight. A polygon inscribed in radius r covers
// less area than a circle of the same r, and needs more pixels before its
// vertices resolve at all: at r=5.5 a hexagon is simply a circle with rough
// edges, which made `user` and `database` the same mark on screen. Agents stay
// largest because they are the subject of the whole console.
export const TYPE_RADIUS = {
  agent: 7,
  database: 7.5,
  tool: 7,
  api: 5.5,
  user: 5,
}

/**
 * Trace an entity-type shape onto a canvas context, centred on (x, y) with
 * circumradius r. Leaves the path open so the caller decides fill vs stroke.
 *
 * The shapes deliberately mirror the ENTITY_ICON glyphs used in the tables, so
 * type reads the same way in both places: agent ◆, user ●, tool ▲, api ■,
 * database ⬢.
 */
export function traceShape(ctx, shape, x, y, r) {
  ctx.beginPath()
  if (shape === 'circle') {
    ctx.arc(x, y, r, 0, 2 * Math.PI)
    ctx.closePath()
    return
  }
  // Polygons are inscribed in the same circumradius so every type reads at a
  // comparable visual weight. `rotation` puts a flat edge or a point where the
  // glyph has one: a diamond stands on a vertex, a square sits square.
  const spec = {
    diamond: { sides: 4, rotation: 0 },
    triangle: { sides: 3, rotation: -Math.PI / 2 },
    square: { sides: 4, rotation: Math.PI / 4 },
    hexagon: { sides: 6, rotation: 0 },
  }[shape] ?? { sides: 4, rotation: 0 }

  for (let i = 0; i < spec.sides; i += 1) {
    const angle = spec.rotation - Math.PI / 2 + (i * 2 * Math.PI) / spec.sides
    const px = x + r * Math.cos(angle)
    const py = y + r * Math.sin(angle)
    if (i === 0) ctx.moveTo(px, py)
    else ctx.lineTo(px, py)
  }
  ctx.closePath()
}
