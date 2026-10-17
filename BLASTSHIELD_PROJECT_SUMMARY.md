# BlastShield — Complete Project Summary

**Project:** BlastShield  
**Hackathon target:** Graph Hacks — Track 01, Agents That Act on Connected Data  
**Project type:** Graph-backed cloud-security investigation and synthetic containment demo  
**Data boundary:** Synthetic local FalkorDB graph only; not a production security product.

## 1. Product overview

BlastShield helps a reviewer follow modeled access from an identity, workload, or IAM role to sensitive data. Instead of presenting an isolated alert score, it queries relationships in FalkorDB and returns paths, entities, edge types, evidence, provenance, deterministic risk factors, and tool traces. A bounded agent can select an approved response action from graph-derived candidates, apply that action to the synthetic graph, and query the graph again to verify what remains.

The product’s central argument is: **FalkorDB provides connected evidence; the agent uses that evidence to choose a response; a fresh graph query verifies the result.** The graph is part of the product’s decision and verification logic, not only a diagram source.

This system does not connect to AWS, Azure, GCP, an identity provider, or production telemetry. Potential reachability is not proof of exploitation, and no path in this modeled graph is not proof that a real environment is safe.

## 2. Architecture

```text
React + Vite dashboard
  ├─ FastAPI: health, alert catalog, canonical investigations, graph depth, containment
  ├─ Read-only Gemini/MCP briefing: fixed investigation tools → grounding check → fallback if needed
  └─ Confirmed autonomous response: Gemini decision → validated controller
       → response-only action capability → fresh canonical graph query
             └─ FalkorDB synthetic graph
```

- **Frontend:** React 19, Vite, React Flow, and React Markdown. It provides a landing page, a scenario workspace, graph/path evidence, a tool trace, a bounded-depth explorer, and the synthetic containment lab.
- **API:** FastAPI owns request validation and routes. Canonical graph investigation outcomes come from the backend adapter and graph services. Health separates API and database state.
- **Graph database:** FalkorDB, selected by the configured graph name (default `blastshield`) through the Python client.
- **Agent integration:** Gemini is optional. MCP exposes typed, fixed tools; the model does not provide Cypher. The application checks generated claims against canonical evidence and labels fallback output distinctly.
- **Response controller:** A separate explicitly confirmed endpoint runs a bounded response loop. The decision model has no graph-mutation tool; only the controller can call the response-only capability after validating an approved candidate.

## 3. Graph model

Node labels:

- `Identity`
- `Session`
- `Workload`
- `IAMRole`
- `DataAsset`

Reviewed access relationship types:

- `HAS_SESSION`
- `CAN_ACCESS`
- `RUNS_AS`
- `CAN_ASSUME`
- `CAN_READ`

Relationships carry properties such as `route_id`, `containment_status`, `response_status`, `assessment`, `granted_actions`, scope, and sensitivity. Traversals are directed and bounded. The query layer limits traversal to the five reviewed relationship types and excludes blocked session routes as appropriate.

`find_shortest_access_path` validates entity IDs and `max_hops` (1–6), then selects among fixed parameterized query templates. It returns ordered node and edge evidence, hop count, and distinct statuses. It does not accept caller-provided Cypher and is **bounded Cypher traversal**, not a native FalkorDB graph-algorithm procedure.

## 4. Canonical scenarios

| Alert | Scenario | Graph target | Verified score | Verified result |
| --- | --- | --- | ---: | --- |
| `BH-1042` | Identity access path | `developer_07` (`Identity`) | 35/100, Medium | One modeled route through session, workload, deployment role, production role, and customer database. |
| `BH-IAM-2001` | Excessive IAM permission | `analytics-role` (`IAMRole`) | 65/100, High | One modeled path includes the graph’s `EXCESSIVE` role-assumption classification. |
| `BH-WORKLOAD-3001` | Public workload blast radius | `exposed-api-01` (`Workload`) | 75/100, High | Three qualifying paths to modeled critical assets. |
| `BH-CONTAINMENT-4001` | Containment verification | `containment-user-01` (`Identity`) | 60/100, High | Two independent active routes to `customer-db` at the freshly seeded baseline. |

Scores use transparent deterministic `demo-rules-v1` rules. They are presentation/demo values, not empirically calibrated probabilities. Scenario 2 consumes an existing `EXCESSIVE` graph classification rather than calculating least privilege itself.

## 5. Bounded graph-depth fixture

An isolated supplemental fixture demonstrates why traversal depth matters without adding a fifth alert or changing the canonical scenarios:

- `auditor_02` → `analytics-cache`: depth 1 returns no path; depth 4 returns a four-hop path.
- `auditor_02` → `staging-report`: depth 4 returns no path; depth 5 returns a five-hop path through `audit-readonly-role` and `staging-observer-role`.
- The live test checks ordered node IDs, directed endpoint pairs, relationship types, and hop count.
- A missing entity returns `unknown_source` or `unknown_target`; known but disconnected entities return `no_path`; query failures remain `query_error`.
- Re-running the graph seed was verified not to increase the existing fixture counts. Existing graph counts can vary because a local graph may retain older synthetic fixture relationships.

## 6. Investigation tools and evidence flow

The fixed MCP catalogue includes:

- `investigate_alert(alert_id)` — canonical graph-backed result.
- `get_blast_radius(alert_id)` — path-derived workload, role, and asset entities.
- `find_shortest_access_path(source_id, target_id, max_hops)` — bounded directed traversal.
- `get_containment_route_statuses(alert_id)` — route-state query for the containment scenario.
- `get_tool_trace()` — actual tool activity and canonical result metadata.

The application records tool names, inputs, outcomes, durations, result counts, and evidence. Model explanations are validated against canonical target/path entities, returned relationship types, and risk score. Unsupported claims or service failures produce a deterministic canonical fallback with fallback status and a diagnostic reason; the UI should not present fallback text as accepted Gemini prose.

## 7. Autonomous response and containment

The response flow is intentionally bounded and synthetic:

1. An operator requests a response and explicitly confirms it.
2. The controller obtains the current canonical graph investigation.
3. Candidate actions are derived from graph-returned path edges and allowed action types.
4. Gemini proposes one candidate; the controller validates it against the approved candidate set and action budget.
5. A separate response-only MCP capability applies the simulation to the synthetic FalkorDB graph.
6. The controller obtains a fresh canonical investigation before deciding whether another action is needed.
7. Completion is reported only after a valid `no_path_in_graph` result and verified action state. Failed verification, unknown/invalid outcomes, and action-limit exhaustion remain stopped/incomplete rather than successful.

The API requires `confirmed: true` and caps `max_actions` at 1–3. Unit tests verify two routes require two actions, the second decision receives fresh alternate-route evidence, no-path terminates without requesting a third action, invalid/unknown outcomes do not become success, and verification failure/action-limit conditions stop safely.

The frontend keeps the actual pre-action investigation snapshot as historical evidence, displays `Before response`, `After response`, and `Compare` views, shows an ordered response timeline with action IDs/timestamps/state transitions/verification, and keeps the current post-action query separate. Historical routes are visually identified as historical/blocked rather than represented as active. The fresh graph correctly has zero qualifying paths after both routes are blocked.

Containment changes only synthetic graph properties. Manual Scenario 4 simulation blocks a selected `HAS_SESSION` route and verifies whether an alternate path remains. Autonomous response uses separate response-state properties for approved synthetic actions. No real cloud session or resource is changed. Containment audit history is in process memory and clears when the API restarts.

## 8. Frontend experience

- Landing page explains the graph-security problem, evidence-based approach, synthetic-data boundary, and workspace CTA.
- The workspace loads the canonical alert catalog and supports URL scenario deep links. Scenario state is cached by alert so changing cases does not overwrite a prior result with `null`; browser `popstate` updates the route.
- Investigation results show outcome, risk assessment/factors, paths, provenance, source graph, and query traces.
- The graph-depth explorer shows status, depth, hop count, nodes, directed edges, and relationship properties.
- Gemini briefing and actual MCP trace are separate from canonical query evidence; fallback states remain distinguishable.
- Containment provides the before/action/after story and route states.
- React Flow is used for graph visualization; the build currently emits a large-chunk advisory but succeeds.

Automated frontend lint and production build pass. Full browser visual QA, mobile behavior, browser navigation regression, and visual overlap checks were not verified in this audit because the available in-app browser could not reach the local Vite server.

## 9. API surface

| Endpoint | Purpose |
| --- | --- |
| `GET /api/health` | API, FalkorDB, and synthetic-mode status. |
| `GET /api/alerts` | Four canonical scenario records. |
| `POST /api/investigations` | Structured canonical graph investigation. |
| `POST /api/graph/shortest-path` | Validated bounded graph-depth query. |
| `POST /api/agent/investigate` | Optional grounded Gemini/MCP explanation and trace. |
| `POST /api/agent/respond` | Explicitly confirmed, bounded autonomous synthetic response. |
| `GET /api/containment/{alert_id}` | Current route status and active-path result. |
| `POST /api/containment/simulate` | Confirmed synthetic route block followed by graph verification. |
| `GET /api/containment/{alert_id}/history` | In-process action audit entries. |

## 10. Local setup and demo commands

One-time setup from the repository root (see `README.md`):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
cd frontend
npm install
cd ..
```

Terminal 1 — start database, seed graph, and API:

```powershell
Set-Location "C:\path\to\blast-shield-graph-hacks"
.\.venv\Scripts\Activate.ps1
docker start blastshield-db
Test-NetConnection localhost -Port 6379
cd backend
python -m app.graph.seed
python -m app.graph.scenario4_seed
python -m uvicorn app.main:app --reload
```

Seed before launching Uvicorn because the server command occupies that terminal. Terminal 2 — start the dashboard:

```powershell
Set-Location "C:\path\to\blast-shield-graph-hacks\frontend"
npm run dev
```

Vite uses `http://localhost:5173`; the API uses `http://localhost:8000`. Optional Gemini configuration uses `GEMINI_API_KEY` and may use `GEMINI_MODEL` in the root `.env`; never commit or disclose credential values. The graph-backed structured investigation remains separate from optional Gemini.

Seed commands use repeatable `MERGE` operations. `scenario4_seed` resets the two synthetic containment routes to `ACTIVE`; scenario seeding is intended for a known demo baseline. Do not run reset/seed operations against a non-demo graph.

## 11. Tests and verified results

Backend unit suite:

```powershell
cd backend
.\venv\Scripts\python.exe -m unittest discover -s tests -v
```

Audit result: 38 tests run, 32 passed, 0 failed, 6 skipped (the live tests are opt-in).

Live FalkorDB tests:

```powershell
cd backend
$env:BLASTSHIELD_RUN_LIVE_TESTS = "1"
.\venv\Scripts\python.exe -m unittest discover -s tests -p "test_live_falkordb.py" -v
Remove-Item Env:BLASTSHIELD_RUN_LIVE_TESTS
```

Audit result: 6 run, 6 passed. These checked canonical investigations, actual depth paths, idempotent fixture seeding, distinct no-path/unknown statuses, containment verification/state restoration, and database health.

Frontend checks:

```powershell
cd frontend
npm run lint
npm run build
```

Both passed. Vite reported a 576.81 kB minified JavaScript bundle (176.81 kB gzip), exceeding the advisory 500 kB warning threshold.

The existing Gemini/MCP smoke test succeeded when its process stdout was set to UTF-8. A four-alert model sweep grounded Identity, IAM, and Containment. Workload had an unsupported-entity fallback on one attempt and grounded on a bounded replay. This is evidence that fallback is active and honestly labeled, not a guarantee that every model answer will ground.

## 12. Track fit and deliberate non-goals

BlastShield demonstrates Track 01 strengths: multi-hop reasoning, graph-backed fixed tools, graph-derived candidate selection, bounded synthetic action, fresh verification, and explainable path evidence. The official track also discusses native graph algorithms, and the overall challenge highlights memory, but this implementation deliberately does **not** claim either of the following:

- Native FalkorDB `algo.SPpaths` (the explorer uses bounded Cypher).
- Persistent graph-backed memory (action history is in-process only).

No additional features, graph expansion, dependency changes, or late visual redesign were made during the final audit.

## 13. Submission handoff

The code and local graph were verified, but the audit did not publish or submit anything. Before final submission:

1. Confirm the source repository is public and the submission URL is correct.
2. Run the browser walkthrough on the demo machine, including the landing CTA, all four scenario deep links, back/forward navigation, depth comparisons, unknown vs disconnected entities, grounded/fallback Gemini, containment before/action/after, graph readability, mobile sizing, and console errors.
3. Record and upload a 2–3 minute demo that shows a real graph path, depth comparison, tool/evidence trace, autonomous containment verification, and the synthetic-data boundary.
4. Select Track 01 in the submission form and verify that all required materials are attached.
5. Check the official rules/deadline before submitting.

See [FINAL_SUBMISSION_READINESS.md](FINAL_SUBMISSION_READINESS.md) for the closeout acceptance table and exact audit results.
