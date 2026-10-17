"""Canonical alert catalog and adapter for graph-backed investigations."""

from __future__ import annotations

from typing import Any

from app.schemas import InvestigationResponse
from app.services import graph_service


# This catalog contains routing metadata only. Investigation facts are always
# queried from the synthetic FalkorDB graph through graph_service.
ALERT_CATALOG: tuple[dict[str, str], ...] = (
    {
        "id": "BH-1042",
        "title": "Identity access path to critical data",
        "status": "NEW",
        "scenario": "identity_investigation",
        "target_type": "Identity",
        "target_id": "developer_07",
    },
    {
        "id": "BH-IAM-2001",
        "title": "Excessive IAM role assumption",
        "status": "NEW",
        "scenario": "excessive_iam_permissions",
        "target_type": "IAMRole",
        "target_id": "analytics-role",
    },
    {
        "id": "BH-WORKLOAD-3001",
        "title": "Public workload can reach critical assets",
        "status": "NEW",
        "scenario": "exposed_workload_blast_radius",
        "target_type": "Workload",
        "target_id": "exposed-api-01",
    },
    {
        "id": "BH-CONTAINMENT-4001",
        "title": "Containment route verification simulation",
        "status": "NEW",
        "scenario": "containment_verification",
        "target_type": "Identity",
        "target_id": "containment-user-01",
    },
)

_INVESTIGATORS = {
    "identity_investigation": graph_service.find_sensitive_resource_paths,
    "excessive_iam_permissions": graph_service.find_excessive_permission_paths,
    "exposed_workload_blast_radius": graph_service.find_workload_blast_radius,
    "containment_verification": graph_service.find_remaining_containment_paths,
}

_STATUS_MAP = {
    "path_found": "path_found",
    "no_path": "no_path_in_graph",
    "unknown_identity": "unknown_entity",
    "unknown_role": "unknown_entity",
    "unknown_workload": "unknown_entity",
    "invalid_input": "invalid_input",
    "query_error": "query_error",
}

_SCENARIO_WARNINGS = {
    "identity_investigation": [
        "This graph query investigates represented access paths; it does not detect login anomalies.",
        "Potential reachability does not prove successful exploitation.",
    ],
    "excessive_iam_permissions": [
        "The query relies on the graph's existing EXCESSIVE permission classification; it does not independently evaluate least privilege.",
        "Potential reachability does not prove successful exploitation.",
    ],
    "exposed_workload_blast_radius": [
        "Exposure and reachability are represented in synthetic graph data; this does not prove compromise or exploitation.",
    ],
    "containment_verification": [
        "Containment changes only the synthetic FalkorDB graph; it does not revoke a live session or change a cloud environment.",
        "No remaining matching path means none was found by the modeled query in this synthetic graph.",
    ],
}


def list_alerts() -> list[dict[str, str]]:
    """Return alert metadata without duplicating graph facts."""
    return [
        {
            "id": item["id"],
            "title": item["title"],
            "status": item["status"],
        }
        for item in ALERT_CATALOG
    ]


def find_alert(alert_id: str) -> dict[str, str] | None:
    return next((item for item in ALERT_CATALOG if item["id"] == alert_id), None)


def _normalize_path(path: dict[str, Any]) -> dict[str, Any]:
    nodes = []
    for node in path.get("nodes", []):
        # Member 1's `label` is the graph label/type, not a display name.
        entity_type = str(node.get("label") or "Entity")
        entity_id = str(node.get("id") or "unknown")
        nodes.append({"id": entity_id, "label": entity_id, "type": entity_type})
    edges = []
    for edge in path.get("edges", []):
        edges.append(
            {
                "source": str(edge.get("source") or ""),
                "target": str(edge.get("target") or ""),
                "type": str(edge.get("type") or "UNKNOWN"),
                "properties": dict(edge.get("properties") or {}),
            }
        )
    return {"nodes": nodes, "edges": edges}


def investigate_alert(alert_id: str) -> InvestigationResponse | None:
    """Resolve an alert and adapt Member 1's deterministic graph result."""
    alert = find_alert(alert_id)
    if alert is None:
        return None

    result = _INVESTIGATORS[alert["scenario"]](alert["target_id"])
    raw_status = str(result.get("status", "query_error"))
    outcome = _STATUS_MAP.get(raw_status, "query_error")
    paths = [_normalize_path(path) for path in result.get("paths", [])]
    evidence: list[str] = []
    for item in result.get("evidence", []):
        ids = " → ".join(item.get("node_ids", []))
        rels = " → ".join(item.get("relationship_types", []))
        evidence.append(f"Path evidence: {ids} ({rels}).")
        additional = item.get("additional_evidence")
        if additional:
            evidence.append("Graph evidence: " + "; ".join(f"{k}={v}" for k, v in additional.items()) + ".")
    if result.get("error"):
        evidence.append(str(result["error"]))

    target_type = alert["target_type"]
    target_id = alert["target_id"]
    if outcome == "path_found":
        summary = f"The synthetic graph contains {len(paths)} qualifying potential access path(s) for {target_id}."
    elif outcome == "no_path_in_graph":
        summary = f"No qualifying path was found for {target_id} by this query in the represented graph. This does not establish that the real environment is safe."
    elif outcome == "unknown_entity":
        summary = f"The alert is known, but its target {target_id} was not found in the graph."
    elif outcome == "invalid_input":
        summary = "The investigation target is invalid."
    else:
        summary = "The graph query failed; no conclusion about reachability can be drawn."

    return InvestigationResponse(
        investigation_id=f"INV-{alert_id}",
        alert_id=alert_id,
        target={"type": target_type, "id": target_id},
        identity_id=target_id if target_type == "Identity" else None,
        outcome=outcome,
        risk_score=None,
        severity="UNASSESSED",
        summary=summary,
        paths=paths,
        evidence=evidence,
        proposed_actions=[],
        warnings=["Data source: synthetic FalkorDB seed data, not a live cloud tenant.", *_SCENARIO_WARNINGS[alert["scenario"]]],
        data_source="synthetic_falkordb",
    )


def get_containment_status(alert_id: str) -> dict[str, Any] | None:
    alert = find_alert(alert_id)
    if not alert or alert["scenario"] != "containment_verification":
        return None
    return graph_service.get_containment_route_statuses(alert["target_id"])


def simulate_containment(alert_id: str, route_id: str) -> dict[str, Any] | None:
    alert = find_alert(alert_id)
    if not alert or alert["scenario"] != "containment_verification":
        return None
    if route_id not in {"primary", "alternate"}:
        return {
            "status": "unknown_route",
            "identity_id": alert["target_id"],
            "route_id": route_id,
            "containment_status": None,
            "error": None,
        }
    return graph_service.simulate_containment(alert["target_id"], route_id)
