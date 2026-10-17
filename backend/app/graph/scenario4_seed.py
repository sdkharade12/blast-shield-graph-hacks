"""Seed synthetic graph data for Scenario 4 containment verification."""

from app.graph.connection import graph_connection


IDENTITY_ID = "containment-user-01"
CRITICAL_ASSET_ID = "customer-db"


def seed_scenario4_graph(graph) -> None:
    """
    Create two independent access routes from one identity
    to a critical data asset.

    The routes exist only in the synthetic BlastShield graph.

    Running this function resets both containment routes to ACTIVE,
    allowing the containment tests to start from a clean baseline.
    """

    # ============================================================
    # STEP 1: CREATE OR UPDATE THE SYNTHETIC NODES
    # ============================================================

    graph.query(
        """
        MERGE (i:Identity {id: 'containment-user-01'})
        SET
            i.name = 'Containment Test Identity',
            i.status = 'ACTIVE'

        MERGE (sp:Session {id: 'containment-session-primary'})
        SET sp.status = 'ACTIVE'

        MERGE (sa:Session {id: 'containment-session-alternate'})
        SET sa.status = 'ACTIVE'

        MERGE (wp:Workload {id: 'containment-workload-primary'})
        SET
            wp.name = 'Primary Workload',
            wp.status = 'RUNNING',
            wp.environment = 'PRODUCTION'

        MERGE (wa:Workload {id: 'containment-workload-alternate'})
        SET
            wa.name = 'Alternate Workload',
            wa.status = 'RUNNING',
            wa.environment = 'PRODUCTION'

        MERGE (rp:IAMRole {id: 'containment-role-primary'})
        SET rp.name = 'Primary Access Role'

        MERGE (ra:IAMRole {id: 'containment-role-alternate'})
        SET ra.name = 'Alternate Access Role'

        MERGE (d:DataAsset {id: 'customer-db'})
        SET
            d.name = 'Customer Database',
            d.sensitivity = 'critical'
        """
    )

    # ============================================================
    # STEP 2: CREATE THE TWO SESSION ACCESS ROUTES
    # ============================================================

    graph.query(
        """
        MATCH
            (i:Identity {id: 'containment-user-01'}),
            (sp:Session {id: 'containment-session-primary'}),
            (sa:Session {id: 'containment-session-alternate'})

        MERGE
            (i)-[primary:HAS_SESSION {route_id: 'primary'}]->(sp)
        SET primary.containment_status = 'ACTIVE'

        MERGE
            (i)-[alternate:HAS_SESSION {route_id: 'alternate'}]->(sa)
        SET alternate.containment_status = 'ACTIVE'
        """
    )

    # ============================================================
    # STEP 3: CONNECT SESSIONS TO WORKLOADS AND IAM ROLES
    # ============================================================

    graph.query(
        """
        MATCH
            (sp:Session {id: 'containment-session-primary'}),
            (sa:Session {id: 'containment-session-alternate'}),
            (wp:Workload {id: 'containment-workload-primary'}),
            (wa:Workload {id: 'containment-workload-alternate'}),
            (rp:IAMRole {id: 'containment-role-primary'}),
            (ra:IAMRole {id: 'containment-role-alternate'})

        MERGE (sp)-[:CAN_ACCESS]->(wp)
        MERGE (wp)-[:RUNS_AS]->(rp)

        MERGE (sa)-[:CAN_ACCESS]->(wa)
        MERGE (wa)-[:RUNS_AS]->(ra)
        """
    )

    # ============================================================
    # STEP 4: CONNECT BOTH ROLES TO THE CRITICAL DATABASE
    # ============================================================

    graph.query(
        """
        MATCH
            (rp:IAMRole {id: 'containment-role-primary'}),
            (ra:IAMRole {id: 'containment-role-alternate'}),
            (d:DataAsset {id: 'customer-db'})

        MERGE (rp)-[:CAN_READ]->(d)
        MERGE (ra)-[:CAN_READ]->(d)
        """
    )


def main() -> None:
    """Initialise and verify the synthetic containment scenario."""

    print("Initialising Scenario 4 graph...")

    with graph_connection() as graph:
        seed_scenario4_graph(graph)

        routes = graph.ro_query(
            """
            MATCH
                (i:Identity {id: 'containment-user-01'})
                -[rel:HAS_SESSION]->
                (s:Session)

            RETURN
                rel.route_id AS route_id,
                rel.containment_status AS containment_status

            ORDER BY route_id
            """
        ).result_set

        assert len(routes) == 2, (
            f"Expected 2 access routes, but found {len(routes)}."
        )

        for route in routes:
            assert route[1] == "ACTIVE", (
                f"Expected an ACTIVE route, but got {route}."
            )

        print("\nScenario 4 graph initialised successfully.")
        print(f"Identity: {IDENTITY_ID}")
        print(f"Critical asset: {CRITICAL_ASSET_ID}")
        print(f"Initial access routes: {len(routes)}")

        for route in routes:
            print(
                f"  {route[0]} route: "
                f"{route[1]}"
            )

        print("\nPASS: Both synthetic access routes are ACTIVE.")


if __name__ == "__main__":
    main()