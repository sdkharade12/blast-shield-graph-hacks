# BlastShield

BlastShield is a graph-native cloud-security investigation demo for the FalkorDB Graph Hacks Track 01. It turns a synthetic cloud access graph into explainable investigation evidence: directed paths, affected assets, deterministic risk factors, bounded MCP tools, and simulated containment verification. It does not connect to a cloud tenant, detect real compromise, or remediate real resources.

## 60-second pitch

Cloud incidents are difficult to triage when identities, sessions, workloads, roles, and data are spread across disconnected tools. BlastShield models those relationships as a graph and asks a concrete question: “what represented access route reaches a critical asset, and what remains if we block one route?” The dashboard shows the answer as traversable evidence—not an opaque score—while preserving the limits of the synthetic model. A reviewer can switch among four cases, inspect every node and relationship, examine actual MCP calls, and safely simulate route blocks.

## Architecture

```text
React investigation dashboard
  ├─ FastAPI: alert catalog, canonical investigation, health, containment
  └─ FastAPI agent endpoint: Gemini explanation ↔ MCP tools
                                  └─ canonical investigation services
                                      └─ FalkorDB synthetic graph
```

The structured investigation endpoint and optional Gemini endpoint are separate. The graph service owns canonical outcomes and evidence; the model can explain that evidence but cannot redefine it. If grounding checks fail or Gemini is unavailable, the agent path falls back to the canonical investigation summary. Health reports API and database status separately.

## Scenarios and seeded targets

| Scenario | Alert | Target | What the graph query demonstrates |
| --- | --- | --- | --- |
| Identity access | `BH-1042` | `developer_07` (`Identity`) | Represented route(s) to critical data assets |
| Excessive IAM | `BH-IAM-2001` | `analytics-role` (`IAMRole`) | Paths through relationships classified `EXCESSIVE` |
| Public workload | `BH-WORKLOAD-3001` | `exposed-api-01` (`Workload`) | Reachability from the exposed workload to critical assets |
| Containment | `BH-CONTAINMENT-4001` | `containment-user-01` (`Identity`) | Active routes before/after a synthetic route block |

Seed data is synthetic. The supplemental audit fixture is intentionally disconnected from canonical alert targets: it includes an audit-reporting path and isolated entities for demonstrating a bounded no-path result. Seed scripts use graph `MERGE` operations and can be rerun; they do not clear the graph. A known baseline snapshot during development was 27 nodes / 24 relationships after both seeds. Existing graph data can change those totals.

## Graph and evidence model

Core labels are `Identity`, `Session`, `Workload`, `IAMRole`, and `DataAsset`. Investigated access relationships are `HAS_SESSION`, `CAN_ACCESS`, `RUNS_AS`, `CAN_ASSUME`, and `CAN_READ`. Relationship properties carry scenario facts such as route ID, containment status, permission classification, scope, and sensitivity. Containment updates only the selected synthetic `HAS_SESSION` route’s `containment_status` property.

Canonical outcomes distinguish `path_found`, `no_path_in_graph`, `unknown_entity`, `invalid_input`, and `query_error`; an unknown alert is separately reported as `unknown_alert`. “No path” means no qualifying path was found by the modeled query, not proof of safety. A path is represented reachability, not proof of exploitation. The IAM scenario consumes the graph’s existing `EXCESSIVE` classification; it does not independently compute least privilege.

`find_shortest_access_path` is a parameterized MCP tool. It accepts source and target IDs, traverses only the five relationship types above, excludes blocked session routes, caps traversal at six hops, and returns the ordered nodes, edges, and hop count. It does not accept caller-provided Cypher. Investigation traces record actual tool names, inputs, outcomes, durations, and result counts. The deterministic `demo-rules-v1` risk score is transparent demonstration logic, not calibrated breach probability.

## Run locally

The project can live at any path. The commands below follow the current Windows setup (`E:\Hackathon\blast-shield-graph`). Ensure the Docker container exposes port 6379 and install dependencies once before the demo.

```powershell
# One-time setup from the repository root
python -m pip install -r requirements.txt
cd frontend
npm install
cd ..
```

```powershell
# Terminal 1 — database, seeds, then API
cd E:\Hackathon\blast-shield-graph
.\.venv\Scripts\Activate.ps1
docker start blastshield-db
Test-NetConnection localhost -Port 6379
cd backend
python -m app.graph.seed
python -m app.graph.scenario4_seed
python -m uvicorn app.main:app --reload
```

```powershell
# Terminal 2 — dashboard
cd E:\Hackathon\blast-shield-graph\frontend
npm run dev
```

The dashboard expects the API on `http://localhost:8000` and Vite on `http://localhost:5173`. The optional agent briefing needs a Gemini key in the repository-root `.env` as `GEMINI_API_KEY=...`; never commit or print the value. Without a configured key, the graph-backed structured investigation and dashboard remain usable and the agent panel reports its unavailable/fallback state.

To reset only the demo containment routes to `ACTIVE`, stop the API if needed and rerun `python -m app.graph.scenario4_seed`. This changes those synthetic route properties; it does not reset or delete the graph volume. For a presentation, seed before starting the API so all fixture data is present.

## Judge demo flow

1. Open the identity case and investigate. Expand evidence to follow the access chain to critical data; point out the result outcome and deterministic risk factors.
2. Switch to excessive IAM and public workload, then return to identity. Results are cached independently per case, so changing scenarios does not turn the earlier result blank or overwrite it.
3. Show graph provenance, the bounded shortest-path result, and the MCP trace. The trace is measured tool activity, not a mock transcript.
4. Open containment. The demo starts with both routes active after `scenario4_seed`: block `primary`, inspect the verified remaining alternate route, then block `alternate` and inspect the verified no-path result. The UI requires explicit confirmation.
5. Rerun the scenario-4 seed after the demo if you want the next presentation to begin with both routes active.

## API endpoints

- `GET /api/health` — API and graph database status, plus synthetic mode.
- `GET /api/alerts` — canonical scenario catalog and graph target IDs.
- `POST /api/investigations` — structured canonical graph result.
- `POST /api/agent/investigate` — optional grounded Gemini/MCP explanation and trace.
- `GET /api/containment/{alert_id}` — current modeled routes and active-path result.
- `POST /api/containment/simulate` — confirmed, synthetic route block followed by graph verification.
- `GET /api/containment/{alert_id}/history` — in-process action audit history (cleared on API restart).

## Tests and verification

Contract/unit tests use mocks and do not need Docker:

```powershell
cd backend
python -m unittest discover -s tests -v
```

Opt-in live tests query the currently seeded FalkorDB graph. Set `BLASTSHIELD_RUN_LIVE_TESTS=1` only when the local container is available:

```powershell
$env:BLASTSHIELD_RUN_LIVE_TESTS = "1"
python -m unittest discover -s tests -p "test_live_falkordb.py" -v
Remove-Item Env:BLASTSHIELD_RUN_LIVE_TESTS
```

Frontend quality checks:

```powershell
cd ..\frontend
npm run lint
npm run build
```

## Boundaries

This is a hackathon prototype over synthetic data. It is not connected to AWS, Azure, GCP, an identity provider, or production telemetry. It does not prove suspicious login activity, compromise, exploitability, or safety. Containment is a reversible demo mutation in FalkorDB only. Do not enter production secrets or use the tool as an authorization/remediation system. AI-generated explanations are optional and are checked against canonical graph evidence; all outputs should be reviewed by a human.
