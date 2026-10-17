"""Read-only MCP capability used only by the confirmed response controller."""
from functools import wraps
from time import perf_counter
from typing import Any

from mcp.server import MCPServer
from app.agent.response_service import response_candidates

mcp = MCPServer("BlastShield Response Decision Tools")
TRACE: list[dict[str, Any]] = []
CHOSEN: dict[str, Any] | None = None


def traced(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        started = perf_counter()
        result = fn(*args, **kwargs)
        TRACE.append({"tool_name": fn.__name__, "input": kwargs or {"alert_id": args[0]} if args else kwargs, "result": result.get("status", "completed"), "duration_ms": round((perf_counter() - started) * 1000), "evidence": [item.get("target_id", "") for item in result.get("items", [])] if isinstance(result, dict) else []})
        return result
    return wrapped


@mcp.tool()
@traced
def get_response_candidates(alert_id: str) -> dict[str, Any]:
    """Return only server-derived, currently actionable synthetic response candidates."""
    return response_candidates(alert_id)


@mcp.tool()
@traced
def choose_response_action(alert_id: str, action_id: str, target_id: str, rationale: str) -> dict[str, Any]:
    """Validate one proposed choice against the current server-derived candidate set. This tool does not mutate."""
    global CHOSEN
    if CHOSEN is not None:
        return {"status": "rejected_multiple_choices", "message": "Choose only one action per controller step."}
    current = response_candidates(alert_id)
    candidate = next((item for item in current.get("items", []) if item["action_id"] == action_id and item["target_id"] == target_id), None)
    if not candidate:
        return {"status": "rejected_candidate_not_current", "action_id": action_id, "target_id": target_id}
    CHOSEN = {**candidate, "rationale": rationale[:600]}
    return {"status": "choice_accepted", "choice": CHOSEN}


@mcp.tool()
def get_response_decision_trace() -> dict[str, Any]:
    """Return the actual read-only decision tool calls made in this response step."""
    return {"items": list(TRACE), "choice": CHOSEN}


if __name__ == "__main__":
    mcp.run()
