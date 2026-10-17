"""Bounded Gemini decision loop with isolated, controller-invoked mutation MCP."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from uuid import uuid4
from typing import Any

from dotenv import load_dotenv
from google import genai
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.agent.response_service import response_candidates
from app.integration import find_alert, investigate_alert

PROJECT_ROOT = Path(__file__).resolve().parents[3]
BACKEND_DIR = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


def _decode_tool_result(result) -> dict[str, Any]:
    data = getattr(result, "structuredContent", None) or getattr(result, "structured_content", None)
    if isinstance(data, dict):
        return data
    text = next((item.text for item in getattr(result, "content", []) if getattr(item, "text", None)), "{}")
    try:
        return json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return {"status": "tool_result_unreadable"}


def _server_params(module: str) -> StdioServerParameters:
    env = os.environ.copy()
    old_path = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = str(BACKEND_DIR) + (os.pathsep + old_path if old_path else "")
    return StdioServerParameters(command=sys.executable, args=["-m", module], env=env)


async def _ask_for_one_action(alert_id: str, investigation: dict[str, Any], timeout_s: float) -> tuple[dict[str, Any] | None, list[dict[str, Any]], str | None]:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        return None, [], "gemini_key_unavailable"
    params = _server_params("app.mcp.response_choice_server")
    prompt = f"""A human explicitly confirmed a synthetic-only response run for alert {alert_id}. Current canonical graph evidence follows as JSON: {json.dumps(investigation, separators=(',', ':'))}
Use get_response_candidates for the current server-derived candidates. Choose exactly one valid candidate by calling choose_response_action with its exact action_id and target_id and a brief evidence-based rationale. Do not invent an action, target, query, or entity. If there is no candidate, do not select one. Return a short explanation. A separate controller—not you—will validate and invoke any action. All data is synthetic; do not imply production remediation."""
    traces: list[dict[str, Any]] = []
    try:
        async with asyncio.timeout(timeout_s):
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    async with genai.Client(api_key=api_key).aio as client:
                        model = os.getenv("GEMINI_MODEL", "gemini-flash-latest")
                        response = await client.models.generate_content(model=model, contents=prompt, config={"tools": [session], "temperature": 0.1, "system_instruction": "You select only a currently returned typed response candidate. Never execute or claim a mutation. Use literal Unicode arrow → in prose; never output LaTeX or MathJax arrow syntax."})
                    trace = _decode_tool_result(await session.call_tool("get_response_decision_trace", {}))
                    traces = trace.get("items", [])
                    choice = trace.get("choice")
                    if choice:
                        return choice, traces, None
                    return None, traces, "no_valid_model_choice"
    except TimeoutError:
        return None, traces, "decision_timeout"
    except Exception as exc:
        return None, traces, f"decision_failed:{type(exc).__name__}"


async def _invoke_response_action(alert_id: str, action_id: str, target_id: str, timeout_s: float) -> tuple[dict[str, Any], int]:
    started = perf_counter()
    params = _server_params("app.mcp.response_action_server")
    async with asyncio.timeout(timeout_s):
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool("execute_simulated_response", {"alert_id": alert_id, "action_id": action_id, "target_id": target_id, "confirmed": True})
                return _decode_tool_result(result), round((perf_counter() - started) * 1000)


async def run_autonomous_response(alert_id: str, max_actions: int = 3) -> dict[str, Any]:
    """Run no more than three model-selected synthetic mutations, verifying each immediately."""
    started_at = datetime.now(timezone.utc)
    started = perf_counter()
    alert = find_alert(alert_id)
    if not alert:
        return {"status": "unknown_alert", "alert_id": alert_id, "actions": []}
    current = investigate_alert(alert_id)
    initial = current.model_dump(mode="json") if current else None
    actions: list[dict[str, Any]] = []
    tool_trace: list[dict[str, Any]] = []
    warnings = ["Synthetic FalkorDB only. No real cloud resource, identity, or session is changed.", "Risk scores are deterministic demo rules, not breach probabilities."]
    stop_reason = "action_limit_reached"
    for index in range(max_actions):
        if not current:
            stop_reason = "graph_query_error"
            break
        if current.outcome == "query_error":
            stop_reason = "graph_query_error"
            break
        if current.outcome == "no_path_in_graph":
            stop_reason = "no_qualifying_path"
            break
        if current.outcome != "path_found":
            stop_reason = current.outcome
            break
        choice, decision_trace, error = await _ask_for_one_action(alert_id, current.model_dump(mode="json"), 45)
        tool_trace.extend({**item, "phase": "decision", "step": index + 1} for item in decision_trace)
        if error:
            stop_reason = error
            break
        if not choice:
            stop_reason = "no_valid_model_choice"
            break
        try:
            result, duration = await _invoke_response_action(alert_id, choice["action_id"], choice["target_id"], 15)
        except Exception as exc:
            stop_reason = f"action_tool_failed:{type(exc).__name__}"
            break
        tool_trace.append({"tool_name": "execute_simulated_response", "phase": "mutation_and_verification", "input": {"alert_id": alert_id, "action_id": choice["action_id"], "target_id": choice["target_id"], "confirmed": True}, "result": result.get("status", "unknown"), "duration_ms": duration, "evidence": [choice["source_id"], choice["target_id"]]})
        if isinstance(result.get("after"), dict):
            try:
                from app.schemas import InvestigationResponse
                current = InvestigationResponse.model_validate(result["after"])
            except Exception:
                current = investigate_alert(alert_id)
        else:
            # The response tool normally returns its post-action investigation.
            # If that evidence is missing, do not reuse the previous snapshot:
            # fetch current graph state before deciding whether another action
            # is needed.
            current = investigate_alert(alert_id)
        actions.append({"sequence": index + 1, "decision": choice, **result})
        if result.get("status") != "verified":
            stop_reason = result.get("status", "verification_failed")
            break
        if current and current.outcome in {"no_path_in_graph", "no_path"}:
            stop_reason = "no_qualifying_path"
            break
        if current and current.outcome != "path_found":
            stop_reason = current.outcome
            break
    else:
        stop_reason = "action_limit_reached"

    final = current.model_dump(mode="json") if current else None
    # A capped run with paths remaining is stopped, not complete. This keeps
    # run completion separate from verified removal of modeled reachability.
    status = "completed" if stop_reason == "no_qualifying_path" and all(item.get("status") == "verified" for item in actions) else "stopped"
    if stop_reason.startswith("gemini_key"):
        warnings.append("Gemini is unavailable; no action was taken.")
    decision_provenance = (
        "Gemini selected a server-validated typed candidate; the API controller invoked a separate response-only MCP mutation tool."
        if actions else f"No action was executed; the controller stopped safely: {stop_reason}."
    )
    return {
        "run_id": f"RESP-{uuid4().hex[:12].upper()}", "alert_id": alert_id,
        "status": status, "started_at": started_at.isoformat(),
        "duration_ms": round((perf_counter() - started) * 1000), "initial_investigation": initial,
        "final_investigation": final, "initial_risk": initial.get("risk_assessment") if initial else None,
        "final_risk": final.get("risk_assessment") if final else None, "actions": actions,
        "verification_steps": [item.get("verification") for item in actions if item.get("verification")],
        "final_outcome": stop_reason, "decision_provenance": decision_provenance,
        "tool_trace": tool_trace, "warnings": warnings,
        "data_source": "synthetic_falkordb",
    }
