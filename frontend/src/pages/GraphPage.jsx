import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import ForceGraph2D from 'react-force-graph-2d'
import EntityDetail from '../components/EntityDetail'
import GraphLegend from '../components/GraphLegend'
import StatTile from '../components/StatTile'
import { Empty, ErrorState, Loading } from '../components/States'
import { useFetch } from '../hooks/useFetch'
import { getAgentGraph, getGraph } from '../lib/api'
import { EDGE_IDLE, HEALTH_COLOR, TYPE_COLOR } from '../lib/palette'

/** Track a container's pixel size so the canvas fills it. */
function useSize() {
  const ref = useRef(null)
  const [size, setSize] = useState({ width: 800, height: 560 })
  useEffect(() => {
    if (!ref.current) return
    const ro = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect
      if (width > 0 && height > 0) setSize({ width, height })
    })
    ro.observe(ref.current)
    return () => ro.disconnect()
  }, [])
  return [ref, size]
}

export default function GraphPage() {
  const { agentId } = useParams()
  const navigate = useNavigate()
  const [depth, setDepth] = useState(2)
  const [selectedId, setSelectedId] = useState(agentId ?? null)
  const [boxRef, size] = useSize()
  const fgRef = useRef(null)

  const { data, error, loading, reload } = useFetch(
    () => (agentId ? getAgentGraph(agentId, depth) : getGraph()),
    [agentId, depth]
  )

  // The side panel always shows a baseline, which only the scoped endpoint
  // computes — so a selection in the full graph fetches it on demand.
  const [detail, setDetail] = useState(null)
  const [detailLoading, setDetailLoading] = useState(false)
  useEffect(() => {
    const node = data?.nodes?.find((n) => n.id === selectedId)
    if (!node || node.type !== 'agent') {
      setDetail(null)
      return
    }
    let cancelled = false
    setDetailLoading(true)
    getAgentGraph(selectedId, 1)
      .then((d) => !cancelled && setDetail(d))
      .catch(() => !cancelled && setDetail(null))
      .finally(() => !cancelled && setDetailLoading(false))
    return () => {
      cancelled = true
    }
  }, [selectedId, data])

  // Default force settings pack a graph this small into the middle of the
  // canvas, which collides the labels. Push the nodes apart and refit once the
  // simulation has actually settled.
  const fitView = useCallback(() => {
    fgRef.current?.zoomToFit(600, 70)
  }, [])

  useEffect(() => {
    const fg = fgRef.current
    if (!fg || graphDataRef.current.nodes.length === 0) return
    fg.d3Force('charge')?.strength(-420).distanceMax(600)
    fg.d3Force('link')?.distance(110).strength(0.6)
    fg.d3ReheatSimulation?.()
    const t = setTimeout(fitView, 900)
    return () => clearTimeout(t)
  })

  // react-force-graph mutates the objects it is handed (it writes x/y onto
  // nodes and swaps link endpoints for node refs), so hand it fresh copies.
  const graphDataRef = useRef({ nodes: [], links: [] })
  const graphData = useMemo(() => {
    if (!data) return { nodes: [], links: [] }
    const next = {
      nodes: data.nodes.map((n) => ({ ...n })),
      links: data.edges.map((e) => ({ ...e })),
    }
    graphDataRef.current = next
    return next
  }, [data])

  const drawNode = useCallback(
    (node, ctx, globalScale) => {
      const r = node.type === 'agent' ? 7 : 5.5
      const ring = HEALTH_COLOR[node.health]
      const isSelected = node.id === selectedId

      ctx.beginPath()
      ctx.arc(node.x, node.y, r, 0, 2 * Math.PI)
      ctx.fillStyle = TYPE_COLOR[node.type] ?? '#6b7488'
      ctx.fill()

      // A 2px surface ring keeps overlapping marks separable, and carries
      // health as a second channel on top of the categorical fill.
      ctx.lineWidth = node.health === 'healthy' ? 1.5 : 2.5
      ctx.strokeStyle = node.health === 'healthy' ? '#12151c' : ring
      ctx.stroke()

      if (isSelected) {
        ctx.beginPath()
        ctx.arc(node.x, node.y, r + 4, 0, 2 * Math.PI)
        ctx.strokeStyle = '#e6e9ef'
        ctx.lineWidth = 1.5
        ctx.stroke()
      }

      const fontSize = Math.max(10 / globalScale, 2.5)
      ctx.font = `${fontSize}px ui-sans-serif, system-ui, sans-serif`
      ctx.textAlign = 'center'
      ctx.textBaseline = 'top'
      ctx.fillStyle = node.health === 'suspicious' ? HEALTH_COLOR.suspicious : '#98a2b8'
      ctx.fillText(node.id, node.x, node.y + r + 2)
    },
    [selectedId]
  )

  if (loading) return <Loading label="Loading behavior graph" />
  if (error) return <ErrorState message={error} onRetry={reload} />

  const stats = data?.stats ?? {}
  const selectedNode = data?.nodes?.find((n) => n.id === selectedId) ?? null

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold">
            Behavior Graph
            {agentId && <span className="text-ink-muted"> · {agentId}</span>}
          </h1>
          <p className="mt-0.5 text-sm text-ink-muted">
            Who talks to whom. Each edge aggregates every event between two
            entities, so volume and suspicion read at a glance.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {agentId ? (
            <>
              <label className="text-xs text-ink-faint">Depth</label>
              <select
                value={depth}
                onChange={(e) => setDepth(Number(e.target.value))}
                className="rounded border border-edge bg-raised px-2 py-1 text-xs text-ink"
              >
                {[1, 2, 3, 4].map((d) => (
                  <option key={d} value={d}>{d} hop{d > 1 ? 's' : ''}</option>
                ))}
              </select>
              <button
                onClick={() => navigate('/graph')}
                className="rounded border border-edge bg-raised px-3 py-1 text-xs text-ink-muted hover:border-accent hover:text-ink"
              >
                View full graph
              </button>
            </>
          ) : (
            selectedNode?.type === 'agent' && (
              <button
                onClick={() => navigate(`/graph/${encodeURIComponent(selectedNode.id)}`)}
                className="rounded border border-edge bg-raised px-3 py-1 text-xs text-ink-muted hover:border-accent hover:text-ink"
              >
                Scope to {selectedNode.id}
              </button>
            )
          )}
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile label="Entities" value={stats.node_count ?? 0} />
        <StatTile label="Relationships" value={stats.edge_count ?? 0} />
        <StatTile
          label="Suspicious entities"
          value={stats.suspicious_node_count ?? 0}
          tone={stats.suspicious_node_count ? 'critical' : 'good'}
          icon={stats.suspicious_node_count ? '▲' : '✓'}
        />
        <StatTile
          label="Suspicious relationships"
          value={stats.suspicious_edge_count ?? 0}
          tone={stats.suspicious_edge_count ? 'critical' : 'good'}
          icon={stats.suspicious_edge_count ? '▲' : '✓'}
        />
      </div>

      <GraphLegend />

      <div className="grid gap-4 lg:grid-cols-[1fr_20rem]">
        <div
          ref={boxRef}
          className="relative h-[560px] overflow-hidden rounded-lg border border-edge bg-panel"
        >
          {graphData.nodes.length === 0 ? (
            <div className="p-6">
              <Empty title="Nothing in the graph yet" hint="Seed the platform to populate it." />
            </div>
          ) : (
            <ForceGraph2D
              ref={fgRef}
              width={size.width}
              height={size.height}
              graphData={graphData}
              backgroundColor="#181d27"
              nodeCanvasObject={drawNode}
              nodePointerAreaPaint={(node, color, ctx) => {
                ctx.fillStyle = color
                ctx.beginPath()
                ctx.arc(node.x, node.y, 10, 0, 2 * Math.PI)
                ctx.fill()
              }}
              nodeLabel={(n) =>
                `${n.id} — ${n.type} · ${n.health} · ${n.event_count} initiated, ${n.inbound_count} received`
              }
              linkColor={(l) => (l.suspicious ? HEALTH_COLOR.suspicious : EDGE_IDLE)}
              linkWidth={(l) => (l.suspicious ? 2.5 : 1)}
              linkDirectionalArrowLength={4}
              linkDirectionalArrowRelPos={1}
              linkDirectionalParticles={(l) => (l.suspicious ? 3 : 0)}
              linkDirectionalParticleWidth={2.5}
              linkDirectionalParticleColor={() => HEALTH_COLOR.suspicious}
              linkLabel={(l) =>
                `${l.source.id ?? l.source} → ${l.target.id ?? l.target} · ${l.action_type} · ${l.count} events${
                  l.permissions?.length ? ` · ${l.permissions.join(', ')}` : ''
                }`
              }
              onNodeClick={(n) => setSelectedId(n.id)}
              onBackgroundClick={() => setSelectedId(null)}
              cooldownTicks={200}
              d3VelocityDecay={0.3}
              onEngineStop={fitView}
            />
          )}
        </div>

        <div className="space-y-3">
          <EntityDetail node={selectedNode} detail={detail} loading={detailLoading} />
        </div>
      </div>
    </div>
  )
}
