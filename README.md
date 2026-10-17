# BlastShield

BlastShield is a local security investigation prototype. Its graph-backed scenarios query **synthetic seeded data in FalkorDB**; they do not read a cloud tenant or perform real remediation.

## Setup

1. Start FalkorDB locally on `localhost:6379` (or set `FALKORDB_HOST`, `FALKORDB_PORT`, and optionally `FALKORDB_GRAPH_NAME`; the graph defaults to `blastshield`).
2. Install the Python dependencies from the repository root: `python -m pip install -r requirements.txt`.
3. Seed scenarios 1–3 with `python -m app.graph.seed` from `backend`, then seed/reset scenario 4 with `python -m app.graph.scenario4_seed` from `backend`.
4. Set `GEMINI_API_KEY` and optionally `GEMINI_MODEL` in the repository-root `.env` for AI explanations. The structured graph investigation endpoints do not require Gemini.
5. Start the API from `backend`: `python -m uvicorn app.main:app --reload`.
6. Start the dashboard from `frontend`: `npm install` then `npm run dev`.

`GET /api/alerts` and `/api/investigations` share one alert catalog and graph adapter. `/api/agent/investigate` is a separate Gemini explanation endpoint. An unavailable graph produces an error; the application does not fall back to mock results. Risk is shown as unassessed because the graph service does not include a deterministic risk engine.

The containment controls require a browser confirmation and change only synthetic route status in FalkorDB. They do not disable real sessions or change cloud resources.

Run the dependency-free adapter and API contract checks from `backend` with `python -m unittest discover -s tests -v`. The live graph service tests require a running FalkorDB instance with the seed scripts applied.
