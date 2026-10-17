
import os
import json
import re
import sys
from time import perf_counter
from pathlib import Path
from dotenv import load_dotenv
from google import genai
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.integration import find_alert, investigate_alert

PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(PROJECT_ROOT / ".env")

BACKEND_DIR = Path(__file__).resolve().parents[2]

SYSTEM_INSTRUCTION = """
You are BlastShield, a cloud-security investigation agent.

Use the available MCP tools to investigate the supplied alert.
Base your findings only on the returned tool results.

Clearly distinguish potential access from confirmed exploitation.
Do not claim real-world containment has occurred.
The tools query a locally seeded synthetic FalkorDB graph, not a live cloud tenant.

If the alert or identity is unknown, say so.
If no qualifying path is returned, do not claim the environment
is safe.

Explain the findings, relevant entities, and limitations simply.
"""


def _deterministic_summary(alert_id: str, result=None) -> str:
    """Safe fallback assembled from canonical evidence, never model prose."""
    result = result or investigate_alert(alert_id)
    if result is None:
        return "The requested alert is unknown."
    entities = []
    for path in result.paths:
        entities.extend(node.id for node in path.nodes)
    unique_entities = ", ".join(dict.fromkeys(entities)) or "no qualifying entities"
    return (
        f"**Canonical investigation:** {result.summary}\n\n"
        f"**Outcome:** `{result.outcome}` · **Evidence entities:** {unique_entities}.\n\n"
        "This result is derived from the synthetic FalkorDB graph and does not establish real-world compromise or remediation."
    )


def _ground_answer(answer: str, alert_id: str, canonical_result=None) -> tuple[str, dict[str, object]]:
    """Reject model output that names unsupported graph entities or scores."""
    result = canonical_result or investigate_alert(alert_id)
    if result is None:
        return _deterministic_summary(alert_id, result), {"status": "fallback", "reason": "unknown_alert"}
    known_ids = {result.target["id"].lower()}
    for path in result.paths:
        known_ids.update(node.id.lower() for node in path.nodes)
    # IDs in this demo always use a hyphen or underscore and include a digit.
    mentioned_ids = set(re.findall(r"\b[a-z][a-z0-9]*(?:[-_][a-z0-9]+)+\b", answer.lower()))
    ignored = {
        "synthetic_falkordb", "falkordb", "real-world",
        result.alert_id.lower(), result.investigation_id.lower(),
        result.provenance.get("dataset_version", "").lower(),
    }
    unsupported = sorted(item for item in mentioned_ids - known_ids - ignored if any(char.isdigit() for char in item))
    score_mentions = re.findall(
        r"\b(?:risk score(?:\s+is|\s+of|\s*:)\s*)?(\d{1,3})\s*(?:/\s*100|%)|\brisk score(?:\s+is|\s+of|\s*:)\s*(\d{1,3})\b",
        answer,
        re.I,
    )
    claimed_scores = [int(first or second) for first, second in score_mentions]
    has_invented_score = any(value != result.risk_score for value in claimed_scores)
    claimed_relationships = {
        relationship for relationship in {
            "HAS_SESSION", "CAN_ACCESS", "RUNS_AS", "CAN_ASSUME", "CAN_READ"
        } if re.search(rf"\b{relationship}\b", answer, re.I)
    }
    returned_relationships = {
        edge.type for path in result.paths for edge in path.edges
    }
    unsupported_relationships = sorted(claimed_relationships - returned_relationships)
    claims_contained = bool(re.search(r"(?:fully|successfully) contained|no remaining access", answer, re.I))
    if unsupported or has_invented_score or unsupported_relationships or (claims_contained and result.outcome == "path_found"):
        return _deterministic_summary(alert_id, result), {
            "status": "fallback",
            "reason": "unsupported_entity_or_claim",
            "unsupported_entities": unsupported,
            "unsupported_relationships": unsupported_relationships,
        }
    return answer, {"status": "grounded", "validated_entity_count": len(known_ids)}


async def run_gemini_investigation(alert_id: str) -> dict[str, object]:
    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set in the backend terminal."
        )

    # Ensure the spawned MCP process can import the app package,
    # regardless of the terminal's original PYTHONPATH.
    child_env = os.environ.copy()
    old_pythonpath = child_env.get("PYTHONPATH", "")

    child_env["PYTHONPATH"] = (
        str(BACKEND_DIR)
        + (os.pathsep + old_pythonpath if old_pythonpath else "")
    )

    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.mcp.server"],
        env=child_env,
    )

    alert = find_alert(alert_id)
    containment_instruction = (
        "3. Call get_containment_route_statuses with the same alert ID."
        if alert and alert["scenario"] == "containment_verification"
        else "3. Do not call containment tools for this non-containment scenario."
    )
    prompt = f"""
Investigate security alert {alert_id}.

1. Call investigate_alert with this alert ID. Its response is the canonical structured investigation.
2. Call get_blast_radius with the same alert ID to summarize path-derived workloads, roles, and assets.
{containment_instruction}
4. When the investigation returns a source entity and a DataAsset ID, call
   find_shortest_access_path for that pair to independently demonstrate the
   bounded graph traversal. Do not call it with guessed entity IDs.
5. Summarise the findings using only the actual tool results and acknowledge their synthetic FalkorDB provenance.

Do not invent missing relationships or permissions.
Do not invent risk scores. If the service reports UNASSESSED, say no deterministic risk score is available.
Do not attempt containment or claim a route was changed; the tools are read-only.
"""

    started_at = perf_counter()
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            async with genai.Client(api_key=api_key).aio as client:
                model_name = os.getenv(
                    "GEMINI_MODEL",
                    "gemini-flash-latest",
                )
                print(f"[Gemini] FastAPI service using model: {model_name}")

                response = await client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config={
                        "tools": [session],
                        "system_instruction": SYSTEM_INSTRUCTION,
                        "temperature": 0.2,
                    },
                )

                answer = response.text
                if not answer:
                    raise RuntimeError(
                        "Gemini returned no text explanation."
                    )

                trace_result = await session.call_tool("get_tool_trace", {})
                trace_data = getattr(trace_result, "structuredContent", None)
                if trace_data is None:
                    trace_data = getattr(trace_result, "structured_content", None)
                if trace_data is None:
                    trace_text = next(
                        (item.text for item in getattr(trace_result, "content", []) if getattr(item, "text", None)),
                        "{}",
                    )
                    trace_data = json.loads(trace_text)
                canonical_data = trace_data.get("canonical_investigation") if isinstance(trace_data, dict) else None
                canonical_result = None
                if canonical_data:
                    from app.schemas import InvestigationResponse
                    canonical_result = InvestigationResponse.model_validate(canonical_data)
                grounded_answer, grounding = _ground_answer(answer, alert_id, canonical_result)
                return {
                    "answer": grounded_answer,
                    "grounding": grounding,
                    "trace": trace_data.get("items", []) if isinstance(trace_data, dict) else [],
                    "grounding_source": "mcp_canonical_investigation" if canonical_result else "canonical_api_fallback",
                    "duration_ms": round((perf_counter() - started_at) * 1000),
                }
