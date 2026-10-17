from typing import Any
from functools import wraps
from time import perf_counter

from mcp.server import MCPServer

from app.integration import (
    find_alert,
    get_containment_status,
    investigate_alert as run_investigation,
)
from app.services.graph_service import find_shortest_access_path as run_shortest_path


mcp = MCPServer("BlastShield Investigation Tools")
TOOL_TRACE: list[dict[str, Any]] = []
CANONICAL_INVESTIGATION: dict[str, Any] | None = None


def _traced(tool_function):
    """Record actual MCP dispatch inputs, outcome, duration and result IDs."""
    @wraps(tool_function)
    def wrapped(*args, **kwargs):
        started = perf_counter()
        result = tool_function(*args, **kwargs)
        inputs = dict(zip(tool_function.__code__.co_varnames, args))
        inputs.update(kwargs)
        entity_ids: list[str] = []

        def collect(value):
            if isinstance(value, dict):
                if isinstance(value.get("id"), str):
                    entity_ids.append(value["id"])
                for child in value.values():
                    collect(child)
            elif isinstance(value, (list, tuple)):
                for child in value:
                    collect(child)

        collect(result)
        route_statuses = []
        if isinstance(result, dict) and isinstance(result.get("routes"), list):
            route_statuses = [
                {
                    "route_id": str(route["route_id"]),
                    "containment_status": str(route["containment_status"]),
                }
                for route in result["routes"]
                if isinstance(route, dict)
                and route.get("route_id") is not None
                and route.get("containment_status") is not None
            ]
        entry = {
            "tool_name": tool_function.__name__,
            "input": {key: str(value) for key, value in inputs.items()},
            "result": str(result.get("status", "completed")) if isinstance(result, dict) else "completed",
            "duration_ms": round((perf_counter() - started) * 1000),
            "result_count": _result_count(result),
            "evidence": list(dict.fromkeys(entity_ids)),
            # Route rows identify graph relationships rather than entities.
            # Preserve their useful state for trace rendering.
            "route_statuses": route_statuses,
        }
        TOOL_TRACE.append(entry)
        del TOOL_TRACE[:-30]
        return result
    return wrapped


def _result_count(result: Any) -> int:
    if not isinstance(result, dict):
        return 0
    for key in ("path_count", "entities", "routes"):
        if key == "path_count" and isinstance(result.get(key), int):
            return result[key]
        if isinstance(result.get(key), list):
            return len(result[key])
    investigation = result.get("investigation")
    if isinstance(investigation, dict):
        return len(investigation.get("paths", []))
    path = result.get("path")
    return int(bool(path))


def _as_dict(result: Any) -> dict[str, Any] | None:
    if result is None:
        return None
    return result.model_dump(mode="json")


@mcp.tool()
@_traced
def investigate_alert(alert_id: str) -> dict[str, Any]:
    """Query canonical graph evidence for a known security alert."""
    global CANONICAL_INVESTIGATION
    if find_alert(alert_id) is None:
        CANONICAL_INVESTIGATION = None
        return {"status": "unknown_alert", "alert_id": alert_id}
    result = _as_dict(run_investigation(alert_id))
    CANONICAL_INVESTIGATION = result
    return {
        # Preserve the canonical investigation outcome. A response object is
        # not automatically a successful security finding.
        "status": result["outcome"] if result else "query_error",
        "investigation": result,
        "data_source": "synthetic_falkordb",
    }


@mcp.tool()
@_traced
def find_shortest_access_path(source_id: str, target_id: str, max_hops: int = 6) -> dict[str, Any]:
    """Find the shortest supported directed path within one to six hops."""
    result = run_shortest_path(source_id, target_id, max_hops=max_hops)
    return {**result, "data_source": "synthetic_falkordb"}


@mcp.tool()
@_traced
def get_blast_radius(alert_id: str) -> dict[str, Any]:
    """Return path-derived workloads, roles and assets for an alert."""
    if find_alert(alert_id) is None:
        return {"status": "unknown_alert", "alert_id": alert_id, "entities": []}
    result = _as_dict(run_investigation(alert_id))
    if result is None:
        return {"status": "query_error", "alert_id": alert_id, "entities": []}
    entities: dict[str, dict[str, str]] = {}
    for path in result["paths"]:
        for node in path["nodes"]:
            if node["type"] in {"Workload", "IAMRole", "DataAsset"}:
                entities[node["id"]] = node
    return {
        "status": result["outcome"],
        "alert_id": alert_id,
        "path_count": len(result["paths"]),
        "entities": list(entities.values()),
        "warnings": result["warnings"],
        "data_source": result["data_source"],
    }


@mcp.tool()
@_traced
def get_containment_route_statuses(alert_id: str) -> dict[str, Any]:
    """Read route status for the synthetic containment scenario."""
    result = get_containment_status(alert_id)
    if result is None:
        return {"status": "unsupported_scenario", "alert_id": alert_id}
    return {**result, "data_source": "synthetic_falkordb"}


@mcp.tool()
def get_tool_trace() -> dict[str, Any]:
    """Return actual MCP calls made during the current agent session."""
    return {
        "items": list(TOOL_TRACE),
        "canonical_investigation": CANONICAL_INVESTIGATION,
    }


if __name__ == "__main__":
    mcp.run()
