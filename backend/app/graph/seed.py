"""Seed the synthetic infrastructure graph for BlastShield."""

from app.graph.connection import graph_connection


def seed_blastshield_graph(graph) -> None:
    """
    Create the synthetic cloud environment for BlastShield.

    Scenario 1:
        Suspicious identity with a potential path to critical data.

    Scenario 2:
        Excessive IAM permissions creating a potential path
        to a critical data asset.

    Scenario 3:
        Exposed workload with access to multiple sensitive assets.

    MERGE is used to avoid duplicating matching nodes and
    relationships when the seeding function runs repeatedly.
    """

    print("Seeding the BlastShield graph...")

    # ---------------------------------------------------------
    # STEP 1: Create or update infrastructure entities
    # ---------------------------------------------------------

    graph.query("""
        // =====================================================
        // SCENARIO 1: IDENTITY INVESTIGATION
        // =====================================================

        MERGE (i:Identity {id: 'developer_07'})
        SET i.name = 'Developer 07',
            i.status = 'ACTIVE'

        MERGE (s:Session {id: 'sess_007'})
        SET s.status = 'ACTIVE'

        MERGE (w:Workload {id: 'build-runner-03'})
        SET w.name = 'Build Runner 03',
            w.status = 'RUNNING',
            w.environment = 'DEVELOPMENT'

        MERGE (r:IAMRole {id: 'deployment-role'})
        SET r.name = 'Deployment Role'

        MERGE (pr:IAMRole {id: 'production-role'})
        SET pr.name = 'Production Role'

        MERGE (d:DataAsset {id: 'customer-db'})
        SET d.name = 'Customer Database',
            d.sensitivity = 'critical'

        // =====================================================
        // SCENARIO 2: EXCESSIVE IAM PERMISSIONS
        // =====================================================

        MERGE (ar:IAMRole {id: 'analytics-role'})
        SET ar.name = 'Analytics Role',
            ar.privilege_level = 'LOW',
            ar.intended_scope = 'ANALYTICS'

        // =====================================================
        // SCENARIO 3: EXPOSED WORKLOAD
        // =====================================================

        MERGE (api:Workload {id: 'exposed-api-01'})
        SET api.name = 'Exposed API Service',
            api.status = 'RUNNING',
            api.environment = 'PRODUCTION',
            api.exposure = 'PUBLIC',
            api.criticality = 'HIGH'

        MERGE (api_role:IAMRole {id: 'api-service-role'})
        SET api_role.name = 'API Service Role',
            api_role.privilege_level = 'MEDIUM',
            api_role.intended_scope = 'API_SERVICE'

        MERGE (payments:DataAsset {id: 'payments-db'})
        SET payments.name = 'Payments Database',
            payments.sensitivity = 'critical'

        MERGE (config:DataAsset {id: 'config-store'})
        SET config.name = 'Configuration Store',
            config.sensitivity = 'critical'

        // Isolated reasoning fixture: an auditor has an in-scope reporting
        // route with a non-critical branch and a longer role-assumption path.
        MERGE (auditor:Identity {id: 'auditor_02'})
        SET auditor.name = 'Auditor 02', auditor.status = 'ACTIVE'

        MERGE (audit_session:Session {id: 'sess_auditor_02'})
        SET audit_session.status = 'ACTIVE'

        MERGE (reporter:Workload {id: 'audit-reporter-02'})
        SET reporter.name = 'Audit Reporter', reporter.status = 'RUNNING',
            reporter.environment = 'DEVELOPMENT'

        MERGE (audit_role:IAMRole {id: 'audit-readonly-role'})
        SET audit_role.name = 'Audit Read Only Role',
            audit_role.intended_scope = 'AUDIT_REPORTING'

        MERGE (staging_role:IAMRole {id: 'staging-observer-role'})
        SET staging_role.name = 'Staging Observer Role',
            staging_role.intended_scope = 'AUDIT_REPORTING'

        MERGE (cache:DataAsset {id: 'analytics-cache'})
        SET cache.name = 'Analytics Cache', cache.sensitivity = 'internal',
            cache.scope = 'AUDIT_REPORTING'

        MERGE (report:DataAsset {id: 'staging-report'})
        SET report.name = 'Staging Audit Report', report.sensitivity = 'internal'

        // Known entities with no qualifying path support a real no-path test.
        MERGE (dormant:Identity {id: 'dormant_identity_03'})
        SET dormant.name = 'Dormant Identity 03', dormant.status = 'INACTIVE'

        MERGE (orphan:DataAsset {id: 'unattached-asset-01'})
        SET orphan.name = 'Unattached Archive', orphan.sensitivity = 'critical'
    """)

    # ---------------------------------------------------------
    # STEP 2: Create or update infrastructure relationships
    # ---------------------------------------------------------

    graph.query("""
        MATCH (i:Identity {id: 'developer_07'})
        MATCH (s:Session {id: 'sess_007'})
        MATCH (w:Workload {id: 'build-runner-03'})
        MATCH (r:IAMRole {id: 'deployment-role'})
        MATCH (pr:IAMRole {id: 'production-role'})
        MATCH (d:DataAsset {id: 'customer-db'})
        MATCH (ar:IAMRole {id: 'analytics-role'})

        MATCH (api:Workload {id: 'exposed-api-01'})
        MATCH (api_role:IAMRole {id: 'api-service-role'})
        MATCH (payments:DataAsset {id: 'payments-db'})
        MATCH (config:DataAsset {id: 'config-store'})

        // Scenario 1: Identity-to-data access path.

        MERGE (i)-[identity_session:HAS_SESSION]->(s)
        SET identity_session.response_status = 'ACTIVE'
        MERGE (s)-[:CAN_ACCESS]->(w)
        MERGE (w)-[:RUNS_AS]->(r)
        MERGE (r)-[:CAN_ASSUME]->(pr)
        MERGE (pr)-[:CAN_READ]->(d)

        // Scenario 2: Excessive role-assumption permission.

        MERGE (ar)-[permission:CAN_ASSUME]->(pr)
        SET permission.assessment = 'EXCESSIVE',
            permission.response_status = 'ACTIVE',
            permission.granted_actions = 'sts:AssumeRole',
            permission.granted_resource_scope = 'production-role',
            permission.intended_scope = 'ANALYTICS',
            permission.reason =
                'Analytics role can assume a production role '
                + 'outside its intended scope'

        // Scenario 3: Exposed workload and its reachable assets.

        MERGE (api)-[:RUNS_AS]->(api_role)
        SET api.response_status = 'ACTIVE'
        MERGE (api_role)-[:CAN_READ]->(d)
        MERGE (api_role)-[:CAN_READ]->(payments)
        MERGE (api_role)-[:CAN_READ]->(config)

    """)

    # Keep the fixture relationships in a separate query. FalkorDB requires a
    # WITH between read clauses and writes in one query; splitting here keeps
    # the seed easy to reason about and safely repeatable.
    graph.query("""
        // This disconnected branch is deliberately separate from the four
        // alert targets so their canonical investigation paths stay stable.
        MATCH (auditor:Identity {id: 'auditor_02'})
        MATCH (audit_session:Session {id: 'sess_auditor_02'})
        MATCH (reporter:Workload {id: 'audit-reporter-02'})
        MATCH (audit_role:IAMRole {id: 'audit-readonly-role'})
        MATCH (staging_role:IAMRole {id: 'staging-observer-role'})
        MATCH (cache:DataAsset {id: 'analytics-cache'})
        MATCH (report:DataAsset {id: 'staging-report'})

        MERGE (auditor)-[:HAS_SESSION]->(audit_session)
        MERGE (audit_session)-[:CAN_ACCESS]->(reporter)
        MERGE (reporter)-[:RUNS_AS]->(audit_role)
        MERGE (audit_role)-[:CAN_READ {assessment: 'WITHIN_SCOPE', granted_actions: 'data:Read', granted_resource_scope: 'AUDIT_REPORTING', intended_scope: 'AUDIT_REPORTING'}]->(cache)
        MERGE (audit_role)-[scope_check:CAN_ASSUME]->(staging_role)
        SET scope_check.assessment = 'WITHIN_SCOPE',
            scope_check.granted_actions = 'sts:AssumeRole',
            scope_check.granted_resource_scope = 'staging-observer-role',
            scope_check.intended_scope = 'AUDIT_REPORTING',
            scope_check.reason = 'Role access stays within the audit reporting environment'
        MERGE (staging_role)-[:CAN_READ]->(report)
        // Second modeled route to the same cache: compare a direct four-hop
        // path with a five-hop role-assumption alternative.
        MERGE (staging_role)-[:CAN_READ]->(cache)
    """)

    print("BlastShield graph seeding complete.")


def main() -> None:
    """Initialise the graph and report its current size."""

    with graph_connection() as graph:
        seed_blastshield_graph(graph)

        # Count nodes.
        node_result = graph.ro_query("""
            MATCH (n)
            RETURN count(n) AS node_count
        """)

        node_count = node_result.result_set[0][0]

        # Count relationships.
        edge_result = graph.ro_query("""
            MATCH ()-[r]->()
            RETURN count(r) AS edge_count
        """)

        edge_count = edge_result.result_set[0][0]

        print("\nBlastShield graph initialised successfully.")
        print(f"Total nodes: {node_count}")
        print(f"Total relationships: {edge_count}")


if __name__ == "__main__":
    main()
