"""Reusable graph investigation services for BlastShield."""

import logging
from typing import Any

from app.graph.connection import graph_connection
from app.graph.queries import (
    GET_IDENTITY_QUERY,
    FIND_SENSITIVE_RESOURCE_PATHS_QUERY,
    GET_IAM_ROLE_QUERY,
    FIND_EXCESSIVE_PERMISSION_PATHS_QUERY,
    GET_WORKLOAD_QUERY,
    FIND_WORKLOAD_BLAST_RADIUS_QUERY,
    GET_CONTAINMENT_IDENTITY_QUERY,
    FIND_REMAINING_CONTAINMENT_PATHS_QUERY,
    SIMULATE_CONTAINMENT_QUERY,
    GET_CONTAINMENT_ROUTE_STATUS_QUERY,
    FIND_SHORTEST_ACCESS_PATH_QUERY,
)

logger = logging.getLogger(__name__)

MAX_ACCESS_PATH_HOPS = 6


# ============================================================
# SHARED GRAPH INVESTIGATION HELPERS
# ============================================================

def _format_paths(
    query_result: list,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """
    Convert FalkorDB query rows into plain dictionaries.

    Each path contains nodes and edges. Queries may also return
    additional evidence used to explain the investigation result.
    """

    paths = []
    evidence = []

    for index, row in enumerate(query_result, start=1):
        nodes = [dict(node) for node in row[0]]

        edges = []

        for edge in row[1]:
            edge_data = dict(edge)

            edge_data["properties"] = dict(
                edge_data.get("properties") or {}
            )

            edges.append(edge_data)

        paths.append({
            "nodes": nodes,
            "edges": edges,
        })

        evidence_item = {
            "path_index": index,
            "node_ids": [
                node["id"] for node in nodes
            ],
            "relationship_types": [
                edge["type"] for edge in edges
            ],
        }

        if len(row) > 2 and row[2] is not None:
            evidence_item["additional_evidence"] = dict(row[2])

        evidence.append(evidence_item)

    return paths, evidence


def _investigate_paths(
    entity_id: str,
    parameter_name: str,
    existence_query: str,
    path_query: str,
    unknown_status: str,
) -> dict[str, Any]:
    """
    Verify an entity exists and retrieve its qualifying graph paths.

    Distinguishes invalid input, unknown entities, entities without
    qualifying paths, and successful investigations.
    """

    if not isinstance(entity_id, str) or not entity_id.strip():
        return {
            "status": "invalid_input",
            parameter_name: entity_id,
            "paths": [],
            "evidence": [],
            "error": "A valid entity ID is required.",
        }

    entity_id = entity_id.strip()

    try:
        with graph_connection() as graph:

            # Step 1: Verify the entity exists.
            entity_result = graph.ro_query(
                existence_query,
                {parameter_name: entity_id},
            ).result_set

            if not entity_result:
                return {
                    "status": unknown_status,
                    parameter_name: entity_id,
                    "paths": [],
                    "evidence": [],
                    "error": None,
                }

            # Step 2: Retrieve qualifying paths.
            query_result = graph.ro_query(
                path_query,
                {parameter_name: entity_id},
            ).result_set

            if not query_result:
                return {
                    "status": "no_path",
                    parameter_name: entity_id,
                    "paths": [],
                    "evidence": [],
                    "error": None,
                }

            # Step 3: Format database results.
            paths, evidence = _format_paths(query_result)

            return {
                "status": "path_found",
                parameter_name: entity_id,
                "paths": paths,
                "evidence": evidence,
                "error": None,
            }

    except Exception:
        logger.exception(
            "Graph investigation failed for %s=%s",
            parameter_name,
            entity_id,
        )

        return {
            "status": "query_error",
            parameter_name: entity_id,
            "paths": [],
            "evidence": [],
            "error": "Graph query failed. Check backend logs.",
        }


# ============================================================
# SCENARIO 1: IDENTITY INVESTIGATION
# ============================================================

def find_sensitive_resource_paths(
    identity_id: str,
) -> dict[str, Any]:
    """
    Find represented access paths from an identity to critical assets.

    Scenario 1: Suspicious-login and identity investigation.
    """

    return _investigate_paths(
        entity_id=identity_id,
        parameter_name="identity_id",
        existence_query=GET_IDENTITY_QUERY,
        path_query=FIND_SENSITIVE_RESOURCE_PATHS_QUERY,
        unknown_status="unknown_identity",
    )


# ============================================================
# SCENARIO 2: EXCESSIVE IAM PERMISSIONS
# ============================================================

def find_excessive_permission_paths(
    role_id: str,
) -> dict[str, Any]:
    """
    Find paths through permissions explicitly classified as excessive.

    Scenario 2: Excessive IAM permissions.
    """

    return _investigate_paths(
        entity_id=role_id,
        parameter_name="role_id",
        existence_query=GET_IAM_ROLE_QUERY,
        path_query=FIND_EXCESSIVE_PERMISSION_PATHS_QUERY,
        unknown_status="unknown_role",
    )


# ============================================================
# SCENARIO 3: EXPOSED WORKLOAD AND BLAST RADIUS
# ============================================================

def find_workload_blast_radius(
    workload_id: str,
) -> dict[str, Any]:
    """
    Find represented paths from a publicly exposed workload
    to reachable critical data assets.

    This identifies graph-represented reachability. It does not
    prove that the workload has been compromised or exploited.
    """

    return _investigate_paths(
        entity_id=workload_id,
        parameter_name="workload_id",
        existence_query=GET_WORKLOAD_QUERY,
        path_query=FIND_WORKLOAD_BLAST_RADIUS_QUERY,
        unknown_status="unknown_workload",
    )


# ============================================================
# SCENARIO 4: FIND REMAINING ACCESS PATHS
# ============================================================

def find_remaining_containment_paths(
    identity_id: str,
) -> dict[str, Any]:
    """
    Find active, represented access paths from an identity
    to critical assets during containment verification.

    A path marked active in the graph indicates potential
    reachability, not proof of successful exploitation.
    """

    return _investigate_paths(
        entity_id=identity_id,
        parameter_name="identity_id",
        existence_query=GET_CONTAINMENT_IDENTITY_QUERY,
        path_query=FIND_REMAINING_CONTAINMENT_PATHS_QUERY,
        unknown_status="unknown_identity",
    )


# ============================================================
# SCENARIO 4: SIMULATE CONTAINMENT
# ============================================================

def simulate_containment(
    identity_id: str,
    route_id: str,
) -> dict[str, Any]:
    """
    Mark a selected synthetic access route as BLOCKED.

    This function modifies only the FalkorDB graph representation.
    It does not disable real sessions or change a cloud environment.

    The operation is idempotent: requesting containment for a route
    that is already BLOCKED does not change its state again.
    """

    # Validate input before contacting FalkorDB.
    if (
        not isinstance(identity_id, str)
        or not identity_id.strip()
        or not isinstance(route_id, str)
        or not route_id.strip()
    ):
        return {
            "status": "invalid_input",
            "identity_id": identity_id,
            "route_id": route_id,
            "containment_status": None,
            "error": "Valid identity and route IDs are required.",
        }

    identity_id = identity_id.strip()
    route_id = route_id.strip()

    try:
        with graph_connection() as graph:

            # Step 1: Confirm that the identity exists.
            identity_result = graph.ro_query(
                GET_CONTAINMENT_IDENTITY_QUERY,
                {"identity_id": identity_id},
            ).result_set

            if not identity_result:
                return {
                    "status": "unknown_identity",
                    "identity_id": identity_id,
                    "route_id": route_id,
                    "containment_status": None,
                    "error": None,
                }

            # Step 2: Retrieve the identity's known routes.
            route_rows = graph.ro_query(
                GET_CONTAINMENT_ROUTE_STATUS_QUERY,
                {"identity_id": identity_id},
            ).result_set

            route_statuses = {
                row[0]: row[1]
                for row in route_rows
            }

            # Step 3: Confirm the selected route exists.
            if route_id not in route_statuses:
                return {
                    "status": "unknown_route",
                    "identity_id": identity_id,
                    "route_id": route_id,
                    "containment_status": None,
                    "error": None,
                }

            # Step 4: Handle repeated containment requests safely.
            if route_statuses[route_id] == "BLOCKED":
                return {
                    "status": "already_blocked",
                    "identity_id": identity_id,
                    "route_id": route_id,
                    "containment_status": "BLOCKED",
                    "error": None,
                }

            # Step 5: Update the synthetic route.
            update_result = graph.query(
                SIMULATE_CONTAINMENT_QUERY,
                {
                    "identity_id": identity_id,
                    "route_id": route_id,
                },
            ).result_set

            if not update_result:
                return {
                    "status": "containment_failed",
                    "identity_id": identity_id,
                    "route_id": route_id,
                    "containment_status": None,
                    "error": "The selected route could not be updated.",
                }

            actual_route_id = update_result[0][0]
            actual_status = update_result[0][1]

            if (
                actual_route_id != route_id
                or actual_status != "BLOCKED"
            ):
                return {
                    "status": "containment_failed",
                    "identity_id": identity_id,
                    "route_id": route_id,
                    "containment_status": actual_status,
                    "error": "The route update could not be verified.",
                }

            return {
                "status": "contained",
                "identity_id": identity_id,
                "route_id": route_id,
                "containment_status": actual_status,
                "error": None,
            }

    except Exception:
        logger.exception(
            "Containment simulation failed for identity=%s, route=%s",
            identity_id,
            route_id,
        )

        return {
            "status": "query_error",
            "identity_id": identity_id,
            "route_id": route_id,
            "containment_status": None,
            "error": "Containment simulation failed. Check backend logs.",
        }


# ============================================================
# SCENARIO 4: VERIFY ROUTE STATUSES
# ============================================================

def get_containment_route_statuses(
    identity_id: str,
) -> dict[str, Any]:
    """
    Retrieve the current synthetic containment status of every
    route represented for the requested identity.
    """

    if not isinstance(identity_id, str) or not identity_id.strip():
        return {
            "status": "invalid_input",
            "identity_id": identity_id,
            "routes": [],
            "error": "A valid identity ID is required.",
        }

    identity_id = identity_id.strip()

    try:
        with graph_connection() as graph:

            # Verify the identity exists first.
            identity_result = graph.ro_query(
                GET_CONTAINMENT_IDENTITY_QUERY,
                {"identity_id": identity_id},
            ).result_set

            if not identity_result:
                return {
                    "status": "unknown_identity",
                    "identity_id": identity_id,
                    "routes": [],
                    "error": None,
                }

            # Retrieve all route statuses.
            route_rows = graph.ro_query(
                GET_CONTAINMENT_ROUTE_STATUS_QUERY,
                {"identity_id": identity_id},
            ).result_set

            routes = [
                {
                    "route_id": row[0],
                    "containment_status": row[1],
                }
                for row in route_rows
            ]

            return {
                "status": "routes_found" if routes else "no_routes",
                "identity_id": identity_id,
                "routes": routes,
                "error": None,
            }

    except Exception:
        logger.exception(
            "Unable to retrieve containment statuses for identity=%s",
            identity_id,
        )

        return {
            "status": "query_error",
            "identity_id": identity_id,
            "routes": [],
            "error": "Route status query failed. Check backend logs.",
        }


# ============================================================
# BOUNDED SHORTEST ACCESS PATH (MCP TOOL)
# ============================================================

def find_shortest_access_path(source_id: str, target_id: str) -> dict[str, Any]:
    """Return the shortest supported directed access path in the graph.

    FalkorDB installations used during demos do not all expose the same graph
    algorithm procedures. This fixed parameterized Cypher query is the
    portable fallback: relationship types and depth are both bounded to six.
    """
    if not all(
        isinstance(value, str) and value.strip() and len(value.strip()) <= 128
        for value in (source_id, target_id)
    ):
        return {
            "status": "invalid_input", "source_id": source_id, "target_id": target_id,
            "path": None, "error": "Valid source and target entity IDs are required.",
        }
    source_id, target_id = source_id.strip(), target_id.strip()
    try:
        with graph_connection() as graph:
            # A label-agnostic lookup is needed because source/target can be any supported entity.
            source_rows = graph.ro_query(
                "MATCH (n {id: $entity_id}) RETURN n.id AS id, coalesce(n.name, n.id) AS name, labels(n)[0] AS type LIMIT 1",
                {"entity_id": source_id},
            ).result_set
            target_rows = graph.ro_query(
                "MATCH (n {id: $entity_id}) RETURN n.id AS id, coalesce(n.name, n.id) AS name, labels(n)[0] AS type LIMIT 1",
                {"entity_id": target_id},
            ).result_set
            if not source_rows:
                return {
                    "status": "unknown_source", "source_id": source_id, "target_id": target_id,
                    "path": None, "hop_count": None, "error": None,
                }
            if not target_rows:
                return {
                    "status": "unknown_target", "source_id": source_id, "target_id": target_id,
                    "path": None, "hop_count": None, "error": None,
                }
            if source_id == target_id:
                source = source_rows[0]
                return {
                    "status": "path_found", "source_id": source_id, "target_id": target_id,
                    "hop_count": 0,
                    "path": {"nodes": [{"id": source[0], "label": source[1], "type": source[2]}], "edges": []},
                    "error": None,
                }
            path_rows = graph.ro_query(
                FIND_SHORTEST_ACCESS_PATH_QUERY,
                {"source_id": source_id, "target_id": target_id},
            ).result_set
        if path_rows:
            row = path_rows[0]
            nodes = [dict(item) for item in row[0]]
            edges = [dict(item) for item in row[1]]
            for edge in edges:
                edge["properties"] = dict(edge.get("properties") or {})
            return {
                "status": "path_found", "source_id": source_id, "target_id": target_id,
                "hop_count": int(row[2]), "path": {"nodes": nodes, "edges": edges}, "error": None,
            }
        return {
            "status": "no_path", "source_id": source_id, "target_id": target_id,
            "path": None, "hop_count": None, "max_hops": MAX_ACCESS_PATH_HOPS, "error": None,
        }
    except Exception:
        logger.exception("Shortest access-path query failed for %s -> %s", source_id, target_id)
        return {
            "status": "query_error", "source_id": source_id, "target_id": target_id,
            "path": None, "error": "Graph query failed. Check backend logs.",
        }
