"""Typed, synthetic-only action candidates and graph-verified mutations."""

from __future__ import annotations

from datetime import datetime, timezone
from time import perf_counter
from typing import Any
from uuid import uuid4

from app.integration import find_alert, investigate_alert
from app.graph.connection import graph_connection


def response_candidates(alert_id: str) -> dict[str, Any]:
    alert = find_alert(alert_id)
    investigation = investigate_alert(alert_id)
    if not alert or not investigation:
        return {"status": "unknown_alert", "items": []}
    if investigation.outcome != "path_found":
        return {"status": investigation.outcome, "items": [], "investigation": investigation.model_dump(mode="json")}

    items = []
    for path in investigation.paths:
        for edge in path.edges:
            props = edge.properties
            if alert["scenario"] == "identity_investigation" and edge.type == "HAS_SESSION" and props.get("response_status", "ACTIVE") == "ACTIVE":
                items.append({"action_id": "block-session", "action_type": "BLOCK_SESSION", "target_id": edge.target, "source_id": edge.source, "relationship": edge.type, "reason": "Block the active synthetic session edge present in the canonical access path."})
            elif alert["scenario"] == "excessive_iam_permissions" and edge.type == "CAN_ASSUME" and props.get("assessment") == "EXCESSIVE" and props.get("response_status", "ACTIVE") == "ACTIVE":
                items.append({"action_id": "block-excessive-assumption", "action_type": "BLOCK_EXCESSIVE_ASSUMPTION", "target_id": edge.target, "source_id": edge.source, "relationship": edge.type, "reason": "Block only this graph-returned CAN_ASSUME relationship classified EXCESSIVE."})
            elif alert["scenario"] == "containment_verification" and edge.type == "HAS_SESSION" and props.get("containment_status") == "ACTIVE":
                items.append({"action_id": f"block-route-{props.get('route_id')}", "action_type": "BLOCK_CONTAINMENT_ROUTE", "target_id": str(props.get("route_id") or ""), "source_id": edge.source, "relationship": edge.type, "reason": "Block this active synthetic route and re-check whether another route remains."})
        if alert["scenario"] == "exposed_workload_blast_radius":
            workload = next((node for node in path.nodes if node.type == "Workload"), None)
            if workload and workload.id == alert["target_id"]:
                items.append({"action_id": "isolate-workload", "action_type": "ISOLATE_WORKLOAD", "target_id": workload.id, "source_id": workload.id, "relationship": "Workload", "reason": "Mark the canonical public workload isolated in synthetic response state; preserve its observed RUNNING status."})

    unique = {(item["action_id"], item["source_id"], item["target_id"]): item for item in items}
    return {"status": "candidates_available" if unique else "no_action_available", "items": list(unique.values()), "investigation": investigation.model_dump(mode="json")}


def execute_response_action(alert_id: str, action_id: str, target_id: str) -> dict[str, Any]:
    """Validate against the current canonical path, mutate one allow-listed item, then re-query."""
    started = perf_counter()
    before = response_candidates(alert_id)
    candidate = next((item for item in before.get("items", []) if item["action_id"] == action_id and item["target_id"] == target_id), None)
    if not candidate:
        return {"status": "rejected_stale_or_unrecognized_candidate", "alert_id": alert_id, "action_id": action_id, "target_id": target_id}

    query_by_type = {
        "BLOCK_SESSION": ("MATCH (:Identity {id: $source_id})-[r:HAS_SESSION]->(:Session {id: $target_id}) WHERE coalesce(r.response_status, 'ACTIVE') = 'ACTIVE' SET r.response_status = 'BLOCKED' RETURN r.response_status AS state", "BLOCKED"),
        "BLOCK_EXCESSIVE_ASSUMPTION": ("MATCH (:IAMRole {id: $source_id})-[r:CAN_ASSUME {assessment: 'EXCESSIVE'}]->(:IAMRole {id: $target_id}) WHERE coalesce(r.response_status, 'ACTIVE') = 'ACTIVE' SET r.response_status = 'BLOCKED' RETURN r.response_status AS state", "BLOCKED"),
        "ISOLATE_WORKLOAD": ("MATCH (w:Workload {id: $target_id, exposure: 'PUBLIC', status: 'RUNNING'}) WHERE coalesce(w.response_status, 'ACTIVE') <> 'ISOLATED' SET w.response_status = 'ISOLATED' RETURN w.response_status AS state", "ISOLATED"),
        "BLOCK_CONTAINMENT_ROUTE": ("MATCH (:Identity {id: $source_id})-[r:HAS_SESSION {route_id: $target_id, containment_status: 'ACTIVE'}]->(:Session) SET r.containment_status = 'BLOCKED' RETURN r.containment_status AS state", "BLOCKED"),
    }
    query_and_expected = query_by_type.get(candidate["action_type"])
    if not query_and_expected:
        return {"status": "rejected_action_type", "action_id": action_id}
    query, expected = query_and_expected
    try:
        with graph_connection() as graph:
            rows = graph.query(query, {"source_id": candidate["source_id"], "target_id": target_id}).result_set
        if not rows or rows[0][0] != expected:
            return {"status": "mutation_not_verified", "action_id": action_id, "target_id": target_id}
    except Exception:
        return {"status": "query_error", "action_id": action_id, "target_id": target_id, "error": "Synthetic graph action failed; check backend logs."}

    after = response_candidates(alert_id)
    after_investigation = after.get("investigation", {})
    verified = after.get("status") in {"no_path_in_graph", "no_path"} or (
        after.get("status") == "candidates_available" and not any(
            item["action_id"] == action_id and item["target_id"] == target_id for item in after.get("items", [])
        )
    )
    return {
        "status": "verified" if verified else "verification_failed",
        "action": {**candidate, "run_action_id": f"ACT-{uuid4().hex[:10].upper()}", "timestamp": datetime.now(timezone.utc).isoformat(), "before_state": "ACTIVE", "after_state": expected, "duration_ms": round((perf_counter() - started) * 1000)},
        "before": before.get("investigation"), "after": after_investigation,
        "remaining_candidates": after.get("items", []),
        "verification": {"fresh_investigation_outcome": after_investigation.get("outcome", after.get("status")), "fresh_risk_assessment": after_investigation.get("risk_assessment"), "target_action_absent": verified},
        "data_source": "synthetic_falkordb",
    }
