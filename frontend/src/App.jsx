import { NavLink, Route, Routes } from 'react-router-dom'
import Agents from './pages/Agents'
import GraphPage from './pages/GraphPage'
import Alerts from './pages/Alerts'
import Delegations from './pages/Delegations'
import Interactions from './pages/Interactions'
import Incidents from './pages/Incidents'
import Forensics from './pages/Forensics'

const Placeholder = ({ name }) => (
  <div className="rounded-lg border border-dashed border-edge bg-panel p-8 text-ink-muted">
    <h2 className="text-heading font-semibold text-ink">{name}</h2>
    <p className="mt-1 text-body">Not built yet.</p>
  </div>
)

const tabs = [
  { to: '/', label: 'Agents', end: true },
  { to: '/incidents', label: 'Incidents' },
  { to: '/graph', label: 'Behavior Graph' },
  { to: '/alerts', label: 'Alerts' },
  { to: '/delegations', label: 'Delegations' },
  { to: '/interactions', label: 'Interactions' },
  { to: '/forensics', label: 'Forensics' },
]

export default function App() {
  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-10 border-b border-edge bg-panel/95 backdrop-blur">
        <div className="mx-auto flex max-w-7xl items-center gap-8 px-6 py-3">
          <div className="flex items-center gap-2">
            <span aria-hidden="true" className="text-accent">◈</span>
            <span className="text-heading font-semibold tracking-tight">
              AI Agent Security
            </span>
          </div>
          <nav className="flex gap-1 text-body">
            {tabs.map((t) => (
              <NavLink
                key={t.to}
                to={t.to}
                end={t.end}
                className={({ isActive }) =>
                  `rounded-md px-3 py-1.5 transition-colors ${
                    isActive
                      ? 'bg-raised font-medium text-ink'
                      : 'text-ink-muted hover:bg-panel hover:text-ink'
                  }`
                }
              >
                {t.label}
              </NavLink>
            ))}
          </nav>
        </div>
      </header>
      <main className="mx-auto max-w-7xl px-6 py-6">
        <Routes>
          <Route path="/" element={<Agents />} />
          <Route path="/graph" element={<GraphPage />} />
          <Route path="/graph/:agentId" element={<GraphPage />} />
          <Route path="/alerts" element={<Alerts />} />
          <Route path="/alerts/:alertId" element={<Alerts />} />
          <Route path="/delegations" element={<Delegations />} />
          <Route path="/delegations/:delegationId" element={<Delegations />} />
          <Route path="/interactions" element={<Interactions />} />
          <Route path="/interactions/:decisionId" element={<Interactions />} />
          <Route path="/incidents" element={<Incidents />} />
          <Route path="/incidents/:incidentId" element={<Incidents />} />
          <Route path="/forensics" element={<Forensics />} />
          <Route path="/forensics/:eventId" element={<Forensics />} />
        </Routes>
      </main>
    </div>
  )
}
