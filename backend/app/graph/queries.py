"""Predefined Cypher queries used by BlastShield."""


# ============================================================
# SCENARIO 1: IDENTITY INVESTIGATION
# ============================================================

GET_IDENTITY_QUERY = """
MATCH (i:Identity {id: $identity_id})
RETURN i.id AS id
LIMIT 1
"""


FIND_SENSITIVE_RESOURCE_PATHS_QUERY = """
MATCH p =
    (i:Identity {id: $identity_id})
    -[:HAS_SESSION]->
    (s:Session {status: 'ACTIVE'})
    -[:CAN_ACCESS]->
    (w:Workload {status: 'RUNNING'})
    -[:RUNS_AS]->
    (r:IAMRole)
    -[:CAN_ASSUME]->
    (pr:IAMRole)
    -[:CAN_READ]->
    (d:DataAsset {sensitivity: 'critical'})

RETURN
    [
        node IN nodes(p) |
        {
            id: node.id,
            label: labels(node)[0]
        }
    ] AS nodes,

    [
        rel IN relationships(p) |
        {
            source: startNode(rel).id,
            target: endNode(rel).id,
            type: type(rel),
            properties: properties(rel)
        }
    ] AS edges
"""


# ============================================================
# SCENARIO 2: EXCESSIVE IAM PERMISSIONS
# ============================================================

GET_IAM_ROLE_QUERY = """
MATCH (r:IAMRole {id: $role_id})
RETURN r.id AS id
LIMIT 1
"""


FIND_EXCESSIVE_PERMISSION_PATHS_QUERY = """
MATCH p =
    (ar:IAMRole {id: $role_id})
    -[permission:CAN_ASSUME {assessment: 'EXCESSIVE'}]->
    (pr:IAMRole)
    -[:CAN_READ]->
    (d:DataAsset {sensitivity: 'critical'})

RETURN
    [
        node IN nodes(p) |
        {
            id: node.id,
            label: labels(node)[0]
        }
    ] AS nodes,

    [
        rel IN relationships(p) |
        {
            source: startNode(rel).id,
            target: endNode(rel).id,
            type: type(rel),
            properties: properties(rel)
        }
    ] AS edges,

    properties(permission) AS excessive_permission
"""


# ============================================================
# SCENARIO 3: EXPOSED WORKLOAD AND BLAST RADIUS
# ============================================================

GET_WORKLOAD_QUERY = """
MATCH (w:Workload {id: $workload_id})
RETURN w.id AS id
LIMIT 1
"""


FIND_WORKLOAD_BLAST_RADIUS_QUERY = """
MATCH p =
    (w:Workload {
        id: $workload_id,
        status: 'RUNNING',
        exposure: 'PUBLIC'
    })
    -[:RUNS_AS]->
    (r:IAMRole)
    -[:CAN_READ]->
    (d:DataAsset {sensitivity: 'critical'})

RETURN
    [
        node IN nodes(p) |
        {
            id: node.id,
            label: labels(node)[0]
        }
    ] AS nodes,

    [
        rel IN relationships(p) |
        {
            source: startNode(rel).id,
            target: endNode(rel).id,
            type: type(rel),
            properties: properties(rel)
        }
    ] AS edges,

    {
        workload_exposure: w.exposure,
        workload_status: w.status,
        workload_criticality: w.criticality,
        role_id: r.id,
        asset_sensitivity: d.sensitivity
    } AS evidence
"""


# ============================================================
# SCENARIO 4: CONTAINMENT VERIFICATION
# ============================================================

GET_CONTAINMENT_IDENTITY_QUERY = """
MATCH (i:Identity {id: $identity_id})
RETURN i.id AS id
LIMIT 1
"""


FIND_REMAINING_CONTAINMENT_PATHS_QUERY = """
MATCH p =
    (i:Identity {id: $identity_id})
    -[route:HAS_SESSION {containment_status: 'ACTIVE'}]->
    (s:Session {status: 'ACTIVE'})
    -[:CAN_ACCESS]->
    (w:Workload {status: 'RUNNING'})
    -[:RUNS_AS]->
    (r:IAMRole)
    -[:CAN_READ]->
    (d:DataAsset {sensitivity: 'critical'})

RETURN
    [
        node IN nodes(p) |
        {
            id: node.id,
            label: labels(node)[0]
        }
    ] AS nodes,

    [
        rel IN relationships(p) |
        {
            source: startNode(rel).id,
            target: endNode(rel).id,
            type: type(rel),
            properties: properties(rel)
        }
    ] AS edges,

    {
        route_id: route.route_id,
        containment_status: route.containment_status,
        asset_id: d.id,
        asset_sensitivity: d.sensitivity,
        role_id: r.id
    } AS evidence
"""


SIMULATE_CONTAINMENT_QUERY = """
MATCH
    (i:Identity {id: $identity_id})
    -[route:HAS_SESSION {route_id: $route_id}]->
    (s:Session)

SET route.containment_status = 'BLOCKED'

RETURN
    route.route_id AS route_id,
    route.containment_status AS containment_status

LIMIT 1
"""


GET_CONTAINMENT_ROUTE_STATUS_QUERY = """
MATCH
    (i:Identity {id: $identity_id})
    -[route:HAS_SESSION]->
    (s:Session)

RETURN
    route.route_id AS route_id,
    route.containment_status AS containment_status

ORDER BY route.route_id
"""