"""Opt-in smoke tests for an already seeded local FalkorDB instance.

Run with BLASTSHIELD_RUN_LIVE_TESTS=1. Containment is exercised through the
same confirmed API handler as the dashboard, then the original route state is
restored even if an assertion fails.
"""

import os
import unittest

from app import integration, main
from app.graph.connection import graph_connection
from app.services import graph_service


LIVE_ENABLED = os.getenv("BLASTSHIELD_RUN_LIVE_TESTS") == "1"


@unittest.skipUnless(LIVE_ENABLED, "set BLASTSHIELD_RUN_LIVE_TESTS=1 to query FalkorDB")
class LiveFalkorDBTests(unittest.TestCase):
    def test_all_canonical_scenarios_query_seeded_graph(self):
        results = [integration.investigate_alert(alert["id"]) for alert in integration.ALERT_CATALOG]
        self.assertEqual(len(results), 4)
        for result in results:
            self.assertEqual(result.outcome, "path_found", f"{result.alert_id}: {result.summary}")
            self.assertTrue(result.paths, result.alert_id)

    def test_shortest_path_found_unknown_and_no_path_outcomes(self):
        found = graph_service.find_shortest_access_path("auditor_02", "staging-report")
        self.assertEqual(found["status"], "path_found")
        self.assertGreater(found["hop_count"], 0)
        self.assertLessEqual(found["hop_count"], graph_service.MAX_ACCESS_PATH_HOPS)
        self.assertEqual(found["path"]["nodes"][0]["id"], "auditor_02")
        self.assertEqual(found["path"]["nodes"][-1]["id"], "staging-report")

        self.assertEqual(
            graph_service.find_shortest_access_path("missing-source", "staging-report")["status"],
            "unknown_source",
        )
        self.assertEqual(
            graph_service.find_shortest_access_path("auditor_02", "missing-target")["status"],
            "unknown_target",
        )
        self.assertEqual(
            graph_service.find_shortest_access_path("auditor_02", "customer-db")["status"],
            "no_path",
        )

    def test_health_separates_api_and_database_status(self):
        health = main.health()
        self.assertEqual(health["api_status"], "ok")
        self.assertEqual(health["database_status"], "ok")
        self.assertEqual(health["status"], "ok")

    def test_containment_sequence_verifies_each_graph_transition_and_restores_state(self):
        alert_id = "BH-CONTAINMENT-4001"
        identity_id = integration.find_alert(alert_id)["target_id"]
        before = graph_service.get_containment_route_statuses(identity_id)
        self.assertEqual(before["status"], "routes_found")
        original = {item["route_id"]: item["containment_status"] for item in before["routes"]}
        self.assertEqual(set(original), {"primary", "alternate"})

        try:
            # Activate only the two existing route properties; do not rerun a
            # whole scenario seed or modify any graph nodes/relationships.
            with graph_connection() as graph:
                for route_id in ("primary", "alternate"):
                    graph.query(
                        "MATCH (:Identity {id: $identity_id})-[r:HAS_SESSION {route_id: $route_id}]->(:Session) "
                        "SET r.containment_status = 'ACTIVE' RETURN r.route_id",
                        {"identity_id": identity_id, "route_id": route_id},
                    )
            initial = graph_service.get_containment_route_statuses(identity_id)
            self.assertTrue(all(route["containment_status"] == "ACTIVE" for route in initial["routes"]))
            pre_action = integration.investigate_alert(alert_id)
            self.assertEqual(pre_action.outcome, "path_found")

            first = main.containment_simulate(
                main.ContainmentSimulationRequest(alert_id=alert_id, route_id="primary", confirmed=True)
            )
            self.assertEqual(first["containment_status"], "BLOCKED")
            self.assertEqual(first["verification_outcome"], "alternative_path_remains")
            self.assertEqual(first["after"]["status"], "path_found")
            self.assertTrue(first["after"]["paths"])

            second = main.containment_simulate(
                main.ContainmentSimulationRequest(alert_id=alert_id, route_id="alternate", confirmed=True)
            )
            self.assertEqual(second["containment_status"], "BLOCKED")
            self.assertEqual(second["verification_outcome"], "no_qualifying_path_in_modeled_graph")
            self.assertEqual(second["after"]["status"], "no_path")
            self.assertFalse(second["after"]["paths"])
            final_routes = second["routes"]
            self.assertTrue(all(route["containment_status"] == "BLOCKED" for route in final_routes))
        finally:
            with graph_connection() as graph:
                for route_id, status in original.items():
                    graph.query(
                        "MATCH (:Identity {id: $identity_id})-[r:HAS_SESSION {route_id: $route_id}]->(:Session) "
                        "SET r.containment_status = $status RETURN r.route_id",
                        {"identity_id": identity_id, "route_id": route_id, "status": status},
                    )
            restored = graph_service.get_containment_route_statuses(identity_id)
            self.assertEqual(
                {item["route_id"]: item["containment_status"] for item in restored["routes"]},
                original,
            )


if __name__ == "__main__":
    unittest.main()
