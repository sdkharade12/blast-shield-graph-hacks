"""Canonical alert catalog and adapter for graph-backed investigations."""

from __future__ import annotations

from datetime import datetime, timezone
from time import perf_counter
from typing import Any
from uuid import uuid4

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

_DATASET_VERSION = "blastshield-synthetic-v1"
_ACTION_HISTORY: list[dict[str, Any]] = []

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
            "scenario": item["scenario"],
            "target_type": item["target_type"],
            "target_id": item["target_id"],
        }
        for item in ALERT_CATALOG
    ]


def find_alert(alert_id: str) -> dict[str, str] | None:
    return next((item for item in ALERT_CATALOG if item["id"] == alert_id), None)


def _normalize_path(path: dict[str, Any]) -> dict[str, Any]:
    nodes = []
    for node in path.get("nodes", []):
        # Member 1's `label` is the graph label/type, not a display name.
        entity_type = str(node.get("type") or node.get("label") or "Entity")
        entity_id = str(node.get("id") or "unknown")
        # Older path providers placed the node type in `label`; newer query
        # maps return a separate display label and type.
        display_label = node.get("label") if node.get("type") else entity_id
        nodes.append({"id": entity_id, "label": str(display_label or entity_id), "type": entity_type})
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

    started_at = perf_counter()
    result = _INVESTIGATORS[alert["scenario"]](alert["target_id"])
    duration_ms = round((perf_counter() - started_at) * 1000)
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
    risk_assessment = _assess_risk(alert["scenario"], outcome, paths)
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
        risk_score=risk_assessment["score"] if risk_assessment else None,
        severity=risk_assessment["severity"] if risk_assessment else "UNASSESSED",
        summary=summary,
        paths=paths,
        evidence=evidence,
        proposed_actions=[],
        warnings=["Data source: synthetic FalkorDB seed data, not a live cloud tenant.", *_SCENARIO_WARNINGS[alert["scenario"]]],
        data_source="synthetic_falkordb",
        trace=[{
            "tool_name": _trace_name(alert["scenario"]),
            "input": {"alert_id": alert_id, "target_id": target_id},
            "result": outcome,
            "duration_ms": duration_ms,
            "result_count": len(paths),
            "evidence": [item["id"] for path in paths for item in path["nodes"]],
        }],
        risk_assessment=risk_assessment,
        provenance={
            "dataset": "Synthetic FalkorDB seed data",
            "dataset_version": _DATASET_VERSION,
            "coverage": "Only modeled identities, workloads, IAM roles, data assets, and reviewed access relationships.",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
    )


def _trace_name(scenario: str) -> str:
    return {
        "identity_investigation": "find_sensitive_resource_paths",
        "excessive_iam_permissions": "find_excessive_permission_paths",
        "exposed_workload_blast_radius": "find_workload_blast_radius",
        "containment_verification": "find_remaining_containment_paths",
    }[scenario]


def _assess_risk(
    scenario: str, outcome: str, paths: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Small transparent demo score; never a real-world breach probability."""
    if outcome != "path_found":
        return None
    factors = ["+35 qualifying modeled path to a critical data asset"]
    score = 35
    if scenario == "exposed_workload_blast_radius":
        score += 30
        factors.append("+30 publicly exposed running workload")
    if scenario == "excessive_iam_permissions":
        score += 30
        factors.append("+30 graph relationship classified EXCESSIVE")
    if scenario == "containment_verification":
        score += min(20, len(paths) * 10)
        factors.append(f"+{min(20, len(paths) * 10)} active independent containment route(s)")
    if len(paths) > 1:
        score += min(15, (len(paths) - 1) * 5)
        factors.append(f"+{min(15, (len(paths) - 1) * 5)} additional represented path(s)")
    score = min(score, 100)
    severity = "CRITICAL" if score >= 80 else "HIGH" if score >= 60 else "MEDIUM" if score >= 30 else "LOW"
    return {
        "score": score,
        "severity": severity,
        "rule_version": "demo-rules-v1",
        "factors": factors,
    }


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
    identity_id = alert["target_id"]
    before = graph_service.find_remaining_containment_paths(identity_id)
    if before["status"] == "query_error":
        return {
            "status": "query_error", "identity_id": identity_id, "route_id": route_id,
            "error": "Pre-action verification query failed.",
        }
    mutation = graph_service.simulate_containment(identity_id, route_id)
    if mutation["status"] not in {"contained", "already_blocked"}:
        return mutation

    # The outcome is determined from a fresh graph investigation after the
    # state change, not just from the route relationship update.
    after = graph_service.find_remaining_containment_paths(identity_id)
    routes = graph_service.get_containment_route_statuses(identity_id)
    if after["status"] == "query_error" or routes["status"] == "query_error":
        verification_outcome = "verification_failed"
    elif after["status"] == "path_found":
        verification_outcome = "alternative_path_remains"
    elif after["status"] == "no_path":
        verification_outcome = "no_qualifying_path_in_modeled_graph"
    else:
        verification_outcome = "verification_failed"

    action = {
        "action_id": f"SIM-{uuid4().hex[:10].upper()}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "route_id": route_id,
        "previous_status": "ACTIVE" if mutation["status"] == "contained" else "BLOCKED",
        "new_status": mutation["containment_status"],
        "verification_outcome": verification_outcome,
    }
    _ACTION_HISTORY.append(action)
    return {
        **mutation,
        "action": action,
        "before": before,
        "after": after,
        "routes": routes.get("routes", []),
        "verification_outcome": verification_outcome,
        "verification_error": after.get("error") or routes.get("error"),
    }


def get_containment_history(alert_id: str) -> list[dict[str, Any]] | None:
    alert = find_alert(alert_id)
    if not alert or alert["scenario"] != "containment_verification":
        return None
    return list(reversed(_ACTION_HISTORY))
