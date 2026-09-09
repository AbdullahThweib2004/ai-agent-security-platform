# AI Agent Security & Control Platform

[![CI](https://github.com/AbdullahThweib2004/ai-agent-security-platform/actions/workflows/ci.yml/badge.svg)](https://github.com/AbdullahThweib2004/ai-agent-security-platform/actions/workflows/ci.yml)

A security layer that monitors and governs autonomous AI agents inside an organization —
their actions, tool calls, delegations, and communication with other agents.

**Five capabilities, all complete.**

1. **Agent Behavior Graph** — every agent action becomes an event; entities and their
   relationships are projected into a graph you can explore and baseline.
2. **Agent Forensics** — reconstruct the full causal chain of any incident by walking
   event parent/child links, with every policy layer's verdict attached.
3. **Delegation Security** — when one agent hands work to another, decide what
   authority travels with it. A delegate receives the least privilege the task
   needs, never an automatic copy of the delegator's permission set.
4. **A2A Security** — before that, decide whether the two agents should be
   interacting at all, judged on identity and trust rather than on what is being
   asked for.
5. **Incident Response** — when independent layers agree an agent has gone wrong,
   contain it automatically: suspend it, refuse its interactions and delegations,
   and record why. Only a named operator lifts containment.

Each layer answers a question the next one takes for granted. Interaction policy
asks *should these two be talking*; delegation asks *what may travel between
them*; containment asks *has this agent forfeited the right to act at all*.

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
| `GET` | `/delegations` | Delegation decisions, filterable by delegator, delegate, decision |
| `GET` | `/delegations/{delegation_id}` | One decision with its permission-by-permission reasoning |
| `POST` | `/agents/{agent_id}/trust` | Assert an agent's trust level (operator action) |
| `GET` | `/agents/{agent_id}/trust` | An agent's identity and trust level |
| `GET` | `/a2a-decisions` | Interaction decisions, filterable by requester, target, decision |
| `GET` | `/a2a-decisions/{decision_id}` | One decision with the trust levels it was made under |
| `GET` | `/incidents` | Containment decisions, filterable by agent and status |
| `GET` | `/incidents/{incident_id}` | One incident with its signals grouped by layer |
| `POST` | `/incidents/{incident_id}/resolve` | Release a contained agent (operator action) |
| `GET` | `/forensics/timeline/{event_id}` | Full causal chain for an event |
| `POST` | `/events/reconcile` | Re-project events that reached Postgres but not the graph |
| `GET` | `/health` | Liveness **and** dependency health (503 if a store is unreachable) |

Status is split in two on purpose. An agent reporting its own actions cannot be
trusted to grade them, so its claim (`reported_status`) is stored verbatim and
never modified, while the platform records its conclusion separately in
`platform_status`. **Every suspicion-aware read path — `/graph`, `/alerts`, the
agent list — consults `platform_status`.** A caller submitting `status` alone is
still accepted; it is treated as `reported_status`.

## Delegation policy (least privilege)

Every `delegation` event is judged permission by permission, on the same ingest
path as everything else — same request, same transaction. Three rules:

| Rule | Effect |
|---|---|
| `confinement` | A delegator can never pass on authority it does not itself hold — hard block |
| `sensitive_category` | Customer data, administrative control, money movement and egress are never auto-granted; reduced to a read-only form where one exists and the delegator holds it, otherwise blocked |
| `unrated_delegate` | A delegate below the cold-start threshold receives nothing, whatever it asked for |

Precedence resolves *attribution*; restriction resolves the *outcome*. Rules run
in the order above and the first block is terminal, but a `limited` verdict is
provisional — a later rule may still downgrade it to blocked, never upgrade it.
That is what makes the unrated rule hold "regardless of what was requested".

A reduction is only granted if the delegator holds the reduced form too.
Otherwise the downgrade would manufacture authority out of nothing and defeat
the confinement rule through the back door.

Delegations declare what they want in `metadata.requested_permissions`. When
absent — as in any emitter predating this feature — `permissions_used` is used
instead, so existing traffic is governed with no backfill and no schema change.

## Interaction policy (A2A)

Before asking what authority travels with a handoff, the platform asks whether
the two agents should be interacting at all. Every **agent → agent** event gets a
verdict — both delegations and the `agent_message` type, which is agents talking
without handing anything over. A user directing an agent is not agent-to-agent
traffic, and a tool, API or database is not an identity that can be trusted.

| Rule | Effect |
|---|---|
| `untrusted_party` | Either side classified `external_untrusted` — refused regardless of what was requested |
| `unrated_counterparty` | The target has too little history to vouch for, and no operator has classified it |
| `default_allow` | Anything else, including internal → internal |

Trust is **hybrid**. `unrated` is derived from clean history through the same
`services/trust.py` threshold the anomaly rules and delegation policy use — one
definition, four consumers. `internal`, `external_trusted` and
`external_untrusted` are asserted by an operator, because no event stream can
tell you whether an agent belongs to your organisation:

```bash
curl -X POST localhost:8000/agents/partner-agent/trust \
     -H 'Content-Type: application/json' -d '{"trust_level": "external_trusted"}'
```

An assertion may be made before an agent has ever been seen — pre-marking a
known-bad counterparty is the point, and requiring a prior event would grant it
one free interaction first. `unrated` is deliberately **not** assertable: it is
the absence of a classification, which the platform derives.

Only the **target** is judged on rating, not the requester. An agent's own first
actions are how it earns a history, so refusing them would mean no agent could
ever become rated — the same reason the anomaly rules decline to judge a
cold-start actor. `external_untrusted` is enforced in **both** directions, since
that is an explicit assertion with no cold-start cost.

### Recording, not enforcing

An interaction verdict does **not** change the event's `platform_status`. This
platform observes traffic after the fact rather than sitting in the request path,
so every decision it makes is a record rather than an intervention. It also
avoids a feedback loop: flagged events are excluded from baselines, so flagging
an agent's first contact would remove that counterparty from the baseline and
make every later contact look novel — blocking the relationship permanently. The
verdict is durable and queryable without poisoning the data it depends on.

### Layering: the gate runs first

Interaction policy evaluates before delegation policy. When it refuses, the
delegation record **cites that decision by id** rather than reaching the same
conclusion under its own rule name — one event, one explanation, two layers.

Only the redundant check is displaced. `confinement` and `sensitive_category`
still evaluate and still fire: a refused conversation says the agents should not
be talking, not that the delegator's permissions changed. In the seeded
incident, all three requested permissions are refused by delegation's *own*
rules while the headline reason points at the upstream block.

"Vouched for" means the same thing in both layers — enough clean history **or**
an operator classification. Accepting history but not an operator's word would
let an operator permit a conversation and still be unable to let anything travel
through it.

## Incident response (automatic containment)

Three layers flag things independently. This asks whether what they have flagged
about one agent, in one window, is enough to stop it acting.

**An incident opens when, within 24 hours, an agent accumulates either:**

- signals from **two or more independent layers, across two or more distinct
  events**, or
- **two or more high-severity alerts**.

Corroboration, not volume, is the discriminator. The three layers were built to
reason independently, so agreement between them is evidence; one layer firing
repeatedly is usually an artefact. A count-based rule opens incidents on
`analyst-1` — a human whose delegation was refused during ordinary work — and on
`finance-agent`'s benign cold-start blocks. This rule fires on exactly one
agent-day in the seeded corpus: the actual attacker.

Four things the threshold deliberately does **not** do:

| | Why |
|---|---|
| Window on ingest time | Replays and backfills land the whole history in one instant, making every agent look like a simultaneous burst and cold-start artefacts indistinguishable from a live attack. The window is measured on `agent_events.timestamp`. |
| Attribute to the target | A block naming a counterparty is often a fact about the *actor* — `confinement` says the delegator lacks a permission — so attributing it would contain a healthy agent that merely received a refused request. |
| Count a delegation that only cites an upstream A2A block | It relayed a conclusion rather than reaching one. Counting it makes a single finding look like two layers agreeing. |
| Judge an unrated actor | During cold start the layers describe what the platform does not know, not what the agent did. Containment is the most severe action here, so it is the last place that should fire on absence of evidence. |

### What containment does

Suspension is an **override inside `trust_level_of()`**, not a parallel
determination. A suspended agent resolves as `external_untrusted`, so every layer
that already asks about trust picks containment up without a second question:
interaction policy refuses it in both directions, and delegation refuses to hand
it anything regardless of how much history it has.

Its events are still **recorded and marked**, never dropped — a contained agent's
actions are exactly the ones an investigator most wants. The marker lives on the
event row rather than being joined from `incidents` at read time, because an
incident's status is mutable: a join would make every event ingested during the
suspension quietly stop looking suspended once the incident was resolved.
Contained activity is also excluded from baselines, so an agent cannot emerge
better-rated than it went in.

### Automatic in, operator-only out

Containment opens automatically; it lifts only through
`POST /incidents/{id}/resolve` with a named operator. The asymmetry is
deliberate. A wrong auto-suspension stops a legitimate agent until a human looks
— visible and recoverable. A wrong auto-release lets a compromised agent resume
silently. Fail toward the noisy error.

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
**243 events raising exactly 6 alerts** — the 236 normal events raise none, so
the incident is not buried in noise. The script prints a ready-to-run
`/forensics/timeline/...` URL for the incident when it finishes.

It also seeds **89 delegation decisions — 72 allowed, 13 limited, 4 blocked** —
so all three least-privilege outcomes are visible without constructing anything
by hand. The daily `finance-agent → reconciliation-agent` handoff is the one to
look at: it asks for `db:write_payment` every day and the answer changes as the
delegate earns a history — refused on day one because finance-agent does not yet
hold the permission, refused on day two because the delegate is still unrated,
and from day three granted `db:read_payment` instead of the write it asked for.

And **67 interaction decisions — 65 allowed, 2 blocked** — one per agent-to-agent
event. The operator onboards the three internal agents at the start, so internal
traffic flows from first contact; `external-agent-x` is never vouched for, so the
incident handoff is refused at the interaction layer as well as the delegation
layer. After the incident the operator marks it `external_untrusted`, and its
next probe is refused on identity alone rather than having to look anomalous all
over again.

Finally **one incident**: `payment-agent` crosses the containment threshold
during the attack and is suspended automatically, with signals from all three
layers recorded as evidence. Its later events are still written to the log —
marked, not dropped — so the containment never becomes a blind spot.

## The dashboard

| Page | Route | What it does |
|---|---|---|
| Agents | `/` | Every agent with its status, alert count, and activity. Suspicious agents sort to the top |
| Behavior Graph | `/graph`, `/graph/{agent_id}` | Interactive force-directed graph. Suspicious nodes carry a red ring, suspicious edges are drawn red and animated. Selecting an agent shows its baseline |
| Alerts | `/alerts`, `/alerts/{alert_id}` | Triage queue sorted by severity, filterable by rule and severity. The detail panel shows the evidence that produced each alert |
| Delegations | `/delegations`, `/delegations/{id}` | Every handoff with a granted/requested ratio for at-a-glance scanning; the detail panel breaks down each requested permission, its verdict, the rule and the reason |
| Interactions | `/interactions`, `/interactions/{id}` | Every agent-to-agent exchange with the trust levels that decided it; a delegation refused upstream links straight to the decision that refused it |
| Incidents | `/incidents`, `/incidents/{id}` | Contained agents, with event time beside detection time, the signals grouped by layer, what was recorded while contained, and a resolve action |
| Forensics | `/forensics`, `/forensics/{event_id}` | Top-to-bottom incident timeline, indented by causal depth, with alerts inline |

Agent status has **three** states, not two:

- **Healthy** — an established baseline and nothing flagged.
- **Suspicious** — one or more rules fired against it.
- **Unrated** — fewer than 3 clean events, so no rule has been able to judge it.
  Shown distinctly because reporting such an agent as "Healthy" would be a false
  reassurance (see *Known limitation: cold start* above).

## Observability

### Health checks

`GET /health` issues a real query against each store — `SELECT 1` on Postgres and
`RETURN 1` on Neo4j — and reports them **individually**, so a caller can tell
which dependency is down rather than only that something is:

```json
{ "status": "healthy", "postgres": "ok", "neo4j": "ok",
  "latency_ms": { "postgres": 0.9, "neo4j": 6.7 } }
```

If either is unreachable the endpoint returns **503** with the specific failure
named:

```json
{ "status": "degraded", "postgres": "ok",
  "neo4j": "error: ServiceUnavailable: Failed to read from defunct connection" }
```

Each probe runs on a worker thread with a short deadline (2s, `HEALTH_TIMEOUT_SECONDS`).
A store that has stopped answering usually *hangs* rather than refusing, and a
health check that hangs with it is what takes the load balancer down alongside
the dependency — so a timeout is reported as a failure rather than waited on.
Both stores are always probed, even after the first fails, because "one thing
broke" and "everything broke" call for different responses.

### Structured logs

Every log line is a single JSON object on stdout, with the interesting fields
hoisted to the top level so they are queryable without regex. Set
`LOG_FORMAT=text` for human-readable output locally, `LOG_LEVEL` to adjust
verbosity.

Each line carries an `event` field naming what happened:

| `event` | Level | Emitted when |
|---|---|---|
| `event.ingest.received` | INFO | An event arrives — ids, types, `reported_status`, `permission_count` |
| `event.ingest.rejected` | WARNING | Refused, with a machine-readable `reason` (`duplicate`, `unknown_parent`, `self_referencing_parent`) |
| `event.rules.evaluated` | INFO | `rules_evaluated`, `rules_fired`, `rules_passed`, and `skipped_reason` when the baseline is too thin to judge |
| `alert.raised` | WARNING | A rule fired — `rule`, `severity`, `alert_id` |
| `event.ingest.completed` | INFO | Both writes committed — `postgres_write`, `neo4j_write`, `status_overridden` |
| `event.ingest.failed` | ERROR | Nothing was written; both sides rolled back |
| `event.ingest.graph_commit_failed` | ERROR | **Postgres committed, graph did not** — carries `remediation` |
| `reconcile.started` / `.completed` | INFO | Backlog size, repaired count, quarantined count |
| `reconcile.event.repaired` | INFO | One event re-projected successfully |
| `reconcile.event.quarantined` | **ERROR** | Still not in the graph — flagged `quarantined: true`, `alert_worthy: true` |

Useful queries:

```bash
# everything about one event, in causal order
docker compose logs backend | jq -c 'select(.event_id=="<uuid>")'

# every alert this agent raised
docker compose logs backend | jq -c 'select(.event=="alert.raised" and .actor_id=="payment-agent")'

# events that are durable but missing from the graph — these need reconciling
docker compose logs backend | jq -c 'select(.event=="event.ingest.graph_commit_failed")'

# anything requiring a human
docker compose logs backend | jq -c 'select(.alert_worthy==true)'
```

**What is deliberately kept out of logs.** Event `metadata` and alert `details`
never appear. They carry the substance of what an agent did — transaction
amounts, query text, counterparties — and that belongs in Postgres behind its
access controls, not duplicated into a log pipeline that is usually readable by
a far wider audience. Logs carry identifiers, types, statuses, rule names,
severities and counts: enough to find and correlate an event, not enough to leak
what it contained. Permissions are counted on receipt rather than copied. There
are tests asserting this, because it is the kind of property that decays
silently.

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

**495 tests, 99% statement coverage.**

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
| `tests/unit/test_logging.py` | JSON log shape: one object per line, extras hoisted for querying |
| `tests/integration/test_ingest_logging.py` | The ingest path's log events and levels, and that metadata never leaks into logs |
| `tests/integration/test_reconcile_logging.py` | Staged-write failure and quarantine logging |
| `tests/integration/test_health.py` | `/health` against genuinely dead servers and a hanging dependency |
| `tests/unit/test_delegation_policy.py` | Each delegation rule in isolation, precedence, and the reduction boundaries |
| `tests/integration/test_delegations_api.py` | Both delegation routes, every filter, and that `requested_permissions` governs over `permissions_used` |
| `tests/unit/test_a2a_policy.py` | Each interaction rule in isolation, precedence, and the rating boundary |
| `tests/integration/test_a2a_api.py` | Both interaction routes, every filter, and that the id a delegation cites actually resolves |
| `tests/integration/test_layering.py` | All four A2A/delegation combinations — the gate replaces only the redundant check |
| `tests/unit/test_incident_policy.py` | The containment threshold, including a case built on replayed timestamps |
| `tests/integration/test_incidents.py` | Detection, suspension, record-and-mark, and operator release |
| `tests/integration/test_incidents_api.py` | Both incident routes, every filter, and a paired test that catches state leaking between runs |

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

## Where this stands

All five original phases are complete and verified end-to-end against live data
and on CI.

| Phase | Capability | Verified by |
|---|---|---|
| 1 | Agent Behavior Graph | 243 events projected into Postgres and Neo4j, counts agreeing across both |
| 2 | Agent Forensics | A 4-hop root-cause trace from a leaf, surfacing a sibling branch off the direct path, with every layer's verdict attached |
| 3 | Delegation Security | 89 decisions — 72 allowed, 13 limited, 4 blocked; least privilege visible as a reduced grant, not just a yes/no |
| 4 | A2A Security | 67 interaction decisions; trust hybrid between derived rating and operator assertion |
| 5 | Incident Response | One agent contained automatically on three-layer corroboration, released only by a named operator |

**495 tests, 99% statement coverage**, all against real Postgres and Neo4j rather
than mocks. Every policy engine has been mutation-tested: 9 mutations on the
delegation rules, 7 on interaction policy, 5 on the layering, and 8 on
containment — all caught, several only after adding the test that isolated them.

### Known outstanding: migrations

The schema is still created with `create_all`. That is now **six schema changes**
deep — `graph_projected`, `delegations`, `agent_identities`, `a2a_decisions`,
nullable identity timestamps, and `incidents` + `incident_events` — and
`create_all` cannot alter an existing table, so each one has required a manual
`DROP TABLE` and reseed on the dev stack.

This has cost nothing so far because none of the data is worth keeping. That
stops being true the first time this runs anywhere real, and Alembic should land
before then. The test suite drops and rebuilds from the models on every run, so
tests cannot drift from the schema — but that is precisely what has kept the
problem invisible.

## Hardening summary

The MVP is hardened across four areas. Each was verified rather than assumed:

| | Covers | Evidence |
|---|---|---|
| **1. Test suite** | Unit, integration and regression tests against **real** Postgres and Neo4j | 495 tests, 99% coverage; mutation-checked — breaking the cold-start threshold, letting suspicious events into baselines, downgrading `blocked`, or swapping `MERGE` for `CREATE` each makes it fail |
| **2. Errors & validation** | Every endpoint audited against bad input; staged-write reconciliation | 38 bad-input cases all return 4xx, none 2xx or 5xx; five real defects found and fixed; the Postgres-committed/graph-failed window asserted as a state transition and healed |
| **3. CI** | ruff, black, full suite against pinned service containers, frontend build, behind one required gate | Green on GitHub; every gate verified by deliberately breaking it on a throwaway branch |
| **4. Observability** | JSON structured logging across the ingest path; real dependency health checks | Logs verified on a live stack; `/health` returns 503 in ~18ms naming the specific dead store; tests assert sensitive metadata never reaches logs |

Known limitations, recorded rather than hidden:

- **Cold start** — an agent with fewer than 3 clean events is `unrated`, not
  judged. Documented above; surfaced distinctly in the UI so it is never
  mistaken for a clean bill of health.
- **No migrations** — the schema is created with `create_all`. Two changes so
  far have needed a manual `DROP TABLE` and reseed in development. Alembic is
  worth adding before there is data worth keeping.

## Build status

- [x] Phase 1 — project scaffold (structure, Docker, configs)
- [x] Phase 2 — backend (models → DB connections → endpoints)
- [x] Phase 3 — seed simulator + verification across Postgres and Neo4j
- [x] Phase 4 — frontend (agent list → graph → alerts → forensics)
- [x] Hardening 1 — automated test suite (176 tests, real databases)
- [x] Hardening 2 — error handling, input validation, staged-write reconciliation
- [x] Hardening 3 — CI pipeline (tests, lint, format, frontend build)
- [x] Hardening 4 — structured JSON logging and real dependency health checks
- [x] Phase 3 — Delegation Security (policy engine, API, tests, UI)
- [x] Phase 4 — A2A Security (identity & trust, interaction policy, layering, API, UI)
- [x] Phase 5 — Incident Response (containment threshold, suspension, resolve, API, UI)
