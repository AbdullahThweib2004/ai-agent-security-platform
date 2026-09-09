# AI Agent Security & Control Platform

A security layer that monitors and governs autonomous AI agents inside an organization —
their actions, tool calls, delegations, and communication with other agents.

**MVP scope (this phase): two capabilities only.**

1. **Agent Behavior Graph** — every agent action becomes an event; entities and their
   relationships are projected into a graph you can explore and baseline.
2. **Agent Forensics** — reconstruct the full causal chain of any incident by walking
   event parent/child links.

Explicitly *not* in scope yet: Delegation Security, A2A Security, Incident Response.

---

## Architecture

```
            ┌──────────────┐
  agents ──▶│  POST /events│───┬──▶  PostgreSQL   (durable, structured event log)
            └──────────────┘   └──▶  Neo4j        (entity relationship graph)
                                          │
   React + Tailwind dashboard ◀── FastAPI ┘
   (agents · graph · alerts · forensics)
```

| Layer | Technology |
|---|---|
| API | Python 3.12 · FastAPI · Pydantic v2 |
| Event log | PostgreSQL 16 (SQLAlchemy 2.0) |
| Graph | Neo4j 5 Community (Bolt driver) |
| UI | React 18 · Vite · TailwindCSS · react-force-graph-2d |
| Runtime | Docker + docker-compose |

## Repository layout

```
.
├── docker-compose.yml        # backend + postgres + neo4j + frontend
├── .env.example              # copy to .env to override defaults
├── backend/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── app/
│   │   ├── main.py           # FastAPI entrypoint
│   │   ├── config.py         # env-driven settings
│   │   ├── db/               # postgres.py · neo4j.py
│   │   ├── models/           # SQLAlchemy ORM (event log)
│   │   ├── schemas/          # Pydantic contracts
│   │   ├── services/         # graph · baseline · anomaly · forensics
│   │   └── routers/          # /events · /graph · /alerts · /forensics
│   └── scripts/
│       └── simulate.py       # seed simulator (banking scenario + anomalies)
└── frontend/
    ├── Dockerfile
    ├── index.html
    └── src/
        ├── App.jsx           # shell + routing
        ├── pages/            # Agents · Graph · Alerts · Forensics
        ├── components/
        └── lib/api.js
```

## Event schema

Every agent action is one **Agent Event**:

| Field | Description |
|---|---|
| `event_id` | UUID, unique per action |
| `timestamp` | UTC time the action occurred |
| `actor_type` / `actor_id` | who acted — `user` or `agent` |
| `target_type` / `target_id` | what was acted on — `agent`, `tool`, `api`, `database` |
| `action_type` | `tool_call`, `delegation`, `data_access`, `api_call` |
| `permissions_used` | list of permissions involved |
| `reported_status` | `allowed`, `blocked`, `suspicious` — what the **caller claimed**. Immutable; never rewritten |
| `platform_status` | `allowed`, `blocked`, `suspicious` — the **platform's own verdict** after the rules ran |
| `metadata` | free-form JSON (amounts, endpoints, reasons, …) |
| `parent_event_id` | links an event to the one that caused it |

## API surface

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/events` | Ingest an event → Postgres + Neo4j |
| `GET` | `/graph` | Full behavior graph |
| `GET` | `/graph/{agent_id}` | Graph scoped to one agent |
| `GET` | `/events` | List events, filterable by actor, target, or either status |
| `GET` | `/events/{event_id}` | A single event |
| `GET` | `/alerts` | Rule-based anomaly findings, with the triggering event inline |
| `GET` | `/alerts/{alert_id}` | A single alert with full detail |
| `GET` | `/forensics/timeline/{event_id}` | Full causal chain for an event |
| `POST` | `/events/reconcile` | Re-project events that reached Postgres but not the graph |
| `GET` | `/health` | Liveness |

Status is split in two on purpose. An agent reporting its own actions cannot be
trusted to grade them, so its claim (`reported_status`) is stored verbatim and
never modified, while the platform records its conclusion separately in
`platform_status`. **Every suspicion-aware read path — `/graph`, `/alerts`, the
agent list — consults `platform_status`.** A caller submitting `status` alone is
still accepted; it is treated as `reported_status`.

## Anomaly rules (rule-based only — no ML in this phase)

| Rule | Fires when |
|---|---|
| `unseen_counterparty` | The agent contacts an agent, tool, API or database it has never contacted |
| `value_excursion` | The action's value exceeds the agent's usual range (historical max x1.5, or mean + 3σ, whichever is tighter) |
| `new_permission` | The agent uses a permission it has never used before |

Baselines are computed on the fly from the agent's own prior events. Two
statuses never contribute to a baseline: `blocked` (what an agent *tried*, not
what it may do) and `suspicious` (what we already flagged). Otherwise an agent
could widen its own definition of normal one anomaly at a time.

### Known limitation: cold start

**An agent with fewer than 3 clean events in its history is not judged at all.**
Its events are ingested and graphed as usual, but no rule fires against them.

This is intentional, not a bug. A baseline built from one or two events makes
every subsequent action look novel, and the resulting alert storm trains whoever
reads it to ignore alerts. The threshold is `MIN_BASELINE_EVENTS` in
`backend/app/services/baseline.py`.

The practical consequence: a brand-new agent gets a grace period, so a freshly
introduced malicious agent will not be flagged on its *own* first actions.
It is still caught from the other direction — the established agent that
contacts it trips `unseen_counterparty` on the inbound edge, which is exactly
how the seeded External Agent scenario surfaces.

## Running it

```bash
cp .env.example .env        # optional; defaults work as-is
docker compose up --build
```

| Service | URL |
|---|---|
| Dashboard | http://localhost:5173 |
| API docs | http://localhost:8000/docs |
| Neo4j Browser | http://localhost:7474 (`neo4j` / `aasec_neo4j_pw`) |
| PostgreSQL | `localhost:5432` (`aasec` / `aasec_pw`) |

Seed the platform with the simulated banking scenario:

```bash
docker compose exec backend python scripts/simulate.py --reset
```

This writes 14 days of ordinary banking traffic (analyst → finance-agent →
payment-agent → bank-api, plus reconciliation runs) and then one chained
incident: an 880,000 payment approved out of nowhere, handed off to an
`external-agent-x` nobody has ever contacted, alongside a bulk read of the
customer database, with the funds finally leaving via `offshore-api`. It seeds
**214 events raising exactly 6 alerts** — the 208 normal events raise none, so
the incident is not buried in noise. The script prints a ready-to-run
`/forensics/timeline/...` URL for the incident when it finishes.

## The dashboard

| Page | Route | What it does |
|---|---|---|
| Agents | `/` | Every agent with its status, alert count, and activity. Suspicious agents sort to the top |
| Behavior Graph | `/graph`, `/graph/{agent_id}` | Interactive force-directed graph. Suspicious nodes carry a red ring, suspicious edges are drawn red and animated. Selecting an agent shows its baseline |
| Alerts | `/alerts`, `/alerts/{alert_id}` | Triage queue sorted by severity, filterable by rule and severity. The detail panel shows the evidence that produced each alert |
| Forensics | `/forensics`, `/forensics/{event_id}` | Top-to-bottom incident timeline, indented by causal depth, with alerts inline |

Agent status has **three** states, not two:

- **Healthy** — an established baseline and nothing flagged.
- **Suspicious** — one or more rules fired against it.
- **Unrated** — fewer than 3 clean events, so no rule has been able to judge it.
  Shown distinctly because reporting such an agent as "Healthy" would be a false
  reassurance (see *Known limitation: cold start* above).

## Error handling

Every endpoint answers bad input with a 4xx and a message a caller can act on.
No stack traces reach a client: unhandled errors become a generic 500, an
unreachable Postgres or Neo4j becomes a 503 saying so. All of these are declared
in the OpenAPI schema, so `/docs` shows the failure shapes alongside the success
case rather than leaving them to be discovered in production.

Four choices worth calling out, because each one turns a silent success into a
loud failure:

- **Unknown fields on `POST /events` are rejected, not ignored.** A caller who
  misspells `permissions_used` and receives `201` believes a permission was
  recorded when nothing was. Extra context belongs in `metadata`, which is
  free-form by design.
- **Blank and whitespace-only identities are rejected**, and valid ones are
  stripped. `"   "` would otherwise become an entity in the behavior graph that
  nobody can name, search for, or reason about.
- **Invalid `severity` / `rule_name` filters return 422, not an empty list.**
  An empty list is indistinguishable from "no alerts" — which is precisely how a
  mistyped filter hides a real finding.
- **Trailing slashes do not redirect.** `GET /graph/` is an empty `agent_id`,
  trivially produced by string interpolation in a client. Redirecting it to
  `GET /graph` would silently answer a scoped question with the entire graph.

### Reconciliation: closing the staged-write window

Postgres is the durable log; Neo4j is a projection of it. They cannot share a
transaction, so ingest stages the two writes — prepare the graph transaction,
commit Postgres, then commit the graph. A failure before the Postgres commit
writes nothing at all. One window remains: **Postgres committed, then the graph
commit failed.** The event is durable and correct but invisible in the graph.

Such an event is left flagged `graph_projected = false` and repaired by:

```bash
curl -X POST localhost:8000/events/reconcile
```

Replaying is safe because every projection is a `MERGE`, so reconciling converges
rather than duplicating. Re-`POST`ing the event is *not* the repair path — the
duplicate guard still returns `409`, which is why the reconciler exists.

This is covered by tests that assert the state transition rather than the log
line: the Postgres row present while the graph is empty, then reconcile, then the
graph populated with no second row and no inflated edge count.

## Tests

The suite runs against a **real Postgres and a real Neo4j**, not mocks. The whole
point of the ingest path is that two stores stay consistent, and a mock cannot
fail in the ways that matter.

```bash
docker compose -f docker-compose.test.yml run --rm tests
```

That spins up throwaway databases on their own ports under their own Compose
project (`aasec-test`), so it never touches the dev stack or its seeded data.
Postgres runs on tmpfs, so every run starts from nothing.

**176 tests, 98% statement coverage.**

| Area | What is pinned |
|---|---|
| `tests/unit/test_anomaly_rules.py` | Each rule in isolation, including the exact firing boundary (`max × 1.5`, strict) and the σ-threshold path |
| `tests/unit/test_baseline.py` | Value extraction, and that blocked/suspicious events never widen a baseline |
| `tests/unit/test_health_state.py` | The three-state grading, including cold start → `unrated` |
| `tests/integration/test_events_api.py` | Ingest, the `reported_status`/`platform_status` split, and that `blocked` is never downgraded |
| `tests/integration/test_graph_api.py` | Both graph routes, suspicion flags, depth, 404s |
| `tests/integration/test_alerts_api.py` | Every filter, composed filters, and that alert evidence is frozen at trigger time |
| `tests/integration/test_forensics_api.py` | Timelines from a leaf, mid-chain, and the root — all reconstructing the same tree |
| `tests/integration/test_regression_incident.py` | The banking incident exactly as validated by hand: 4-hop root-cause recovery plus the sibling `customer-db` branch |
| `tests/integration/test_graph_projection.py` | MERGE idempotency: three identical events → one edge, `count=3` |
| `tests/integration/test_error_handling.py` | Every endpoint against bad input: malformed JSON, unknown enums, blank identities, orphan parents, bad UUIDs, out-of-range params, and that no 500 ever leaks internals |
| `tests/integration/test_staged_write_failure.py` | The Postgres-committed / graph-failed window, asserted as a state transition and healed by the reconciler |

The suite is mutation-checked — breaking the cold-start threshold, letting
suspicious events back into baselines, downgrading `blocked`, or swapping the
graph's `MERGE` for `CREATE` each makes it fail.

## Continuous integration

`.github/workflows/ci.yml` runs on every push and pull request.

| Job | Gates |
|---|---|
| **backend** | `ruff check` · `black --check` · full pytest suite against Postgres 16 and Neo4j 5 service containers · coverage artifact |
| **frontend** | `npm install` · frontend tests (when a `test` script exists) · `npm run build` |
| **ci** | A single required status check that fails unless **both** jobs succeeded — a skipped or cancelled job is not a pass |

Service images are pinned to the same versions as `docker-compose.yml`; CI passing
against engines nobody runs would prove nothing. The suite drops and rebuilds the
schema from the models each run (`tests/conftest.py`), so CI and local cannot drift.

Caching covers the pip and npm **download** caches only — dependency resolution
and installation still run every time, so a yanked or broken dependency fails the
build instead of being masked by a restored `node_modules` or `site-packages`.

Service-container isolation is left to the platform: GitHub gives each job its own
network and per-run container names. A hardcoded `--name` would be *worse* — on a
self-hosted runner two concurrent jobs would collide on the same fixed name.

The workflow was executed locally with [`act`](https://github.com/nektos/act)
rather than assumed correct, and each gate was verified by deliberately breaking
it: a failing assertion, an unused import, bad formatting, an unresolvable
frontend import, and a failed job feeding the gate. All five failed the build.

## Build status

- [x] Phase 1 — project scaffold (structure, Docker, configs)
- [x] Phase 2 — backend (models → DB connections → endpoints)
- [x] Phase 3 — seed simulator + verification across Postgres and Neo4j
- [x] Phase 4 — frontend (agent list → graph → alerts → forensics)
- [x] Hardening 1 — automated test suite (176 tests, real databases)
- [x] Hardening 2 — error handling, input validation, staged-write reconciliation
- [x] Hardening 3 — CI pipeline (tests, lint, format, frontend build)
