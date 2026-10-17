# BlastShield — Complete Hackathon Progress Summary

This document consolidates the work completed across this Codex conversation and the next useful steps for continuing the hackathon project.

## Project goal

Prepare BlastShield as a polished, reliable hackathon demonstration: improve the investigation dashboard, preserve independent scenario results, make graph evidence and AI/tool activity inspectable, strengthen the seeded graph and containment story, verify backend/MCP/Gemini behavior, and provide a repeatable demo and setup guide.

The app remains a synthetic-data prototype. It does not connect to a real cloud tenant, confirm compromise, or remediate live resources.

## Work completed

### Dashboard and scenario flow

- Reworked the frontend into a security-investigation command dashboard with a clear header, separate API/database health, scenario navigation, selected-case details, investigation outcome and deterministic risk, graph topology, evidence/provenance disclosures, AI briefing, tool trace, and containment controls.
- Added responsive layouts, keyboard/focus affordances, accessible scenario buttons and labels, reduced-motion handling, and distinct error/empty/loading states.
- Fixed scenario switching so each alert keeps its own investigation, graph result, and agent briefing when navigating between scenarios. Previously, switching away from Scenario 1 cleared its visible result.
- Added request cancellation/version checks to prevent late responses from a previously selected case from overwriting the current case.
- Containment changes invalidate old evidence for that case and refresh the graph investigation after the action, so the UI does not present pre-action evidence as current.
- The frontend uses the API alert catalog for target IDs instead of duplicating the target map in the UI.

### Backend and graph behavior

- Health now reports `api_status` and `database_status` independently while retaining an aggregate status for compatibility.
- Canonical result handling distinguishes findings, no modeled path, unknown entities, invalid input, unknown alerts, and graph query errors.
- Added a parameterized, database-side shortest-access-path query with a six-hop limit and a relationship allow-list: `HAS_SESSION`, `CAN_ACCESS`, `RUNS_AS`, `CAN_ASSUME`, and `CAN_READ`. Blocked session routes are excluded. The tool does not accept arbitrary Cypher.
- Path results include node IDs, display labels, node types, directed relationships, relationship properties, and hop count.
- Added synthetic, disconnected audit-reporting graph fixtures, including a reachable audit path and isolated entities for a demonstrable no-path result. They do not connect to the canonical alert targets.
- Seed operations use merge/update patterns and were run repeatedly without clearing the database. The observed graph after seeding was 27 nodes and 24 relationships (it was 18/18 before the supplemental fixture); counts can vary if the graph is changed later.
- Containment simulation requires explicit confirmation, verifies the route mutation in FalkorDB, then re-queries remaining access paths and route statuses. The action history is an in-process audit list and resets when the API process restarts.

### MCP, risk, and AI grounding

- Added measured MCP call tracing for investigation, blast radius, bounded shortest path, and containment route status tools. Traces include tool name, inputs, result, duration, result count, and returned entity IDs; the recent-call history is bounded.
- Kept structured investigations separate from the optional Gemini agent endpoint.
- The deterministic `demo-rules-v1` risk score exposes its factors and is clearly described as demo logic, not a calibrated breach probability.
- Tightened Gemini answer checks against canonical entities, scores, returned relationship types, and containment status. Unsupported claims fall back to a canonical evidence summary.
- Adjusted the agent instructions so containment status is queried only for the containment scenario, not for unrelated scenarios.
- The configured Gemini integration was exercised end-to-end with synthetic data. The final run returned `grounded`, used the MCP investigation, blast-radius, and shortest-path tools, and reported its trace. The Gemini SDK emitted a non-blocking recommendation about its automatic function-calling API.

### Documentation and test coverage

- Expanded [README.md](README.md) with a concise pitch, architecture, scenario/target table, graph evidence model, relationship allow-list, limitations, exact run commands, judging/demo sequence, API endpoints, test commands, and data/AI disclosures.
- Added opt-in live FalkorDB tests, separate from the default mock/contract suite. They check the four canonical cases, found/unknown/no-path shortest-path outcomes, health reporting, and the full containment sequence.
- The containment live test temporarily sets only the two existing synthetic route-status properties, validates primary-block → alternate-path-remains → alternate-block → no-path, and restores the exact original statuses in a `finally` block. It does not reset the Redis/FalkorDB volume.

## Files to know

- [Dashboard state and UI](frontend/src/App.jsx)
- [Dashboard styles](frontend/src/App.css)
- [Graph queries](backend/app/graph/queries.py)
- [Graph service, including shortest path and containment](backend/app/services/graph_service.py)
- [Canonical scenario catalog and result mapping](backend/app/integration.py)
- [FastAPI routes and health](backend/app/main.py)
- [MCP tools and trace](backend/app/mcp/server.py)
- [Gemini integration and grounding](backend/app/agent/gemini_service.py)
- [Contract tests](backend/tests/test_integration.py)
- [Opt-in live graph tests](backend/tests/test_live_falkordb.py)
- [Project setup and demo guide](README.md)

## Verification performed

- Backend contract/unit suite: 17 tests passed. The normal discovery run also reports the four live checks as skipped unless explicitly enabled.
- Live FalkorDB suite: 4 tests passed against the local seeded database.
- Frontend ESLint: passed.
- Frontend production build: passed.
- Gemini/MCP smoke test: passed with canonical grounding and actual MCP trace.
- Seed repeatability: supplemental seed completed twice; graph entities were merged rather than the graph being cleared.
- Live route cleanup: the containment suite verified that pre-test route statuses were restored.

## Known caveats

- The production build has a non-blocking warning that its main JavaScript chunk is slightly above 500 kB after minification. A future pass could split or lazy-load the graph visualization bundle.
- Screenshot-level browser QA was not completed: the Codex in-app browser could not reach the local Vite server, although lint and production build passed.
- Live tests require a running local FalkorDB container and `BLASTSHIELD_RUN_LIVE_TESTS=1`.
- Gemini is optional and requires `GEMINI_API_KEY` in the repository-root `.env`. The key was checked without printing its value. Do not commit or expose it.
- The app’s graph, alerts, risk, and containment are synthetic. A “no path” result means none was found by the modeled query; it is not proof of safety. The excessive-IAM scenario consumes graph labels already classified `EXCESSIVE`; it does not independently evaluate least privilege.

## Run commands

The project’s original local path is `E:\Hackathon\blast-shield-graph`.

```powershell
# Terminal 1 — graph and API
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

The scenario-4 seed sets both containment routes to `ACTIVE` for a fresh presentation. Rerun it when you want to restore that demo baseline; it does not wipe the database volume.

## Suggested next moves

1. Run the app using the commands above and visually review it at desktop and narrow/mobile widths; the local browser preview could not be reached during this pass.
2. Rehearse the sequence: identity investigation → excessive IAM → public workload → containment primary block → alternate remains → alternate block → no modeled path.
3. Decide whether the optional bundle-splitting optimization is worthwhile for the judging environment.
4. Before submission, review the synthetic-data and AI disclosures, verify the demo starts from both routes active, and keep the `.env` out of any commit or screen share.

