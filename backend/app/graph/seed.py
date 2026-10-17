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

        MERGE (i)-[:HAS_SESSION]->(s)
        MERGE (s)-[:CAN_ACCESS]->(w)
        MERGE (w)-[:RUNS_AS]->(r)
        MERGE (r)-[:CAN_ASSUME]->(pr)
        MERGE (pr)-[:CAN_READ]->(d)

        // Scenario 2: Excessive role-assumption permission.

        MERGE (ar)-[permission:CAN_ASSUME]->(pr)
        SET permission.assessment = 'EXCESSIVE',
            permission.reason =
                'Analytics role can assume a production role '
                + 'outside its intended scope'

        // Scenario 3: Exposed workload and its reachable assets.

        MERGE (api)-[:RUNS_AS]->(api_role)
        MERGE (api_role)-[:CAN_READ]->(d)
        MERGE (api_role)-[:CAN_READ]->(payments)
        MERGE (api_role)-[:CAN_READ]->(config)
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