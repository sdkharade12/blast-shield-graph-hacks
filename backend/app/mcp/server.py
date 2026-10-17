from typing import Any

from mcp.server import MCPServer

from app.integration import (
    find_alert,
    get_containment_status,
    investigate_alert as run_investigation,
)


mcp = MCPServer("BlastShield Investigation Tools")


def _as_dict(result: Any) -> dict[str, Any] | None:
    if result is None:
        return None
    return result.model_dump(mode="json")


@mcp.tool()
def investigate_alert(alert_id: str) -> dict[str, Any]:
    """Query canonical graph evidence for a known security alert."""
    if find_alert(alert_id) is None:
        return {"status": "unknown_alert", "alert_id": alert_id}
    result = _as_dict(run_investigation(alert_id))
    return {
        "status": "found" if result else "query_error",
        "investigation": result,
        "data_source": "synthetic_falkordb",
    }


@mcp.tool()
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
def get_containment_route_statuses(alert_id: str) -> dict[str, Any]:
    """Read route status for the synthetic containment scenario."""
    result = get_containment_status(alert_id)
    if result is None:
        return {"status": "unsupported_scenario", "alert_id": alert_id}
    return {**result, "data_source": "synthetic_falkordb"}


if __name__ == "__main__":
    mcp.run()
