// Thin client over the FastAPI backend.
export const API_BASE = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000'

async function get(path) {
  const res = await fetch(`${API_BASE}${path}`)
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`
    try {
      const body = await res.json()
      if (body?.detail) detail = body.detail
    } catch {
      /* response had no JSON body; the status line is all we have */
    }
    throw new Error(detail)
  }
  return res.json()
}

async function post(path, body) {
  const res = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body ?? {}),
  })
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`
    try {
      const parsed = await res.json()
      if (parsed?.detail) {
        // Validation failures come back as a list of field errors, not a string.
        detail = Array.isArray(parsed.detail)
          ? parsed.detail.map((d) => d.msg).join('; ')
          : parsed.detail
      }
    } catch {
      /* response had no JSON body; the status line is all we have */
    }
    throw new Error(detail)
  }
  return res.json()
}

export const getGraph = () => get('/graph')
export const getAgentGraph = (agentId, depth = 1) =>
  get(`/graph/${encodeURIComponent(agentId)}?depth=${depth}`)
export const getAlerts = (params = {}) => {
  const q = new URLSearchParams(
    Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '')
  ).toString()
  return get(`/alerts${q ? `?${q}` : ''}`)
}
export const getAlert = (alertId) => get(`/alerts/${alertId}`)
export const getTimeline = (eventId) => get(`/forensics/timeline/${eventId}`)

export const getDelegations = (params = {}) => {
  const q = new URLSearchParams(
    Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '')
  ).toString()
  return get(`/delegations${q ? `?${q}` : ''}`)
}
export const getDelegation = (delegationId) => get(`/delegations/${delegationId}`)

export const getA2ADecisions = (params = {}) => {
  const q = new URLSearchParams(
    Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '')
  ).toString()
  return get(`/a2a-decisions${q ? `?${q}` : ''}`)
}
export const getA2ADecision = (decisionId) => get(`/a2a-decisions/${decisionId}`)

export const getIncidents = (params = {}) => {
  const q = new URLSearchParams(
    Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '')
  ).toString()
  return get(`/incidents${q ? `?${q}` : ''}`)
}
export const getIncident = (incidentId) => get(`/incidents/${incidentId}`)
export const resolveIncident = (incidentId, body) =>
  post(`/incidents/${incidentId}/resolve`, body)
export const getEvents = (params = {}) => {
  const q = new URLSearchParams(
    Object.entries(params).filter(([, v]) => v !== undefined && v !== null && v !== '')
  ).toString()
  return get(`/events${q ? `?${q}` : ''}`)
}
