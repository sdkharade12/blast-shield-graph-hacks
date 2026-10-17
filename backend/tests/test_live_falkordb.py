"""Opt-in smoke tests for an already seeded local FalkorDB instance.

Run with BLASTSHIELD_RUN_LIVE_TESTS=1. Containment is exercised through the
same confirmed API handler as the dashboard, then the original route state is
restored even if an assertion fails.
"""

import os
import unittest

from app import integration, main
from app.graph.connection import graph_connection
from app.graph.seed import seed_blastshield_graph
from app.services import graph_service
from app.agent.response_service import response_candidates, execute_response_action


LIVE_ENABLED = os.getenv("BLASTSHIELD_RUN_LIVE_TESTS") == "1"


@unittest.skipUnless(LIVE_ENABLED, "set BLASTSHIELD_RUN_LIVE_TESTS=1 to query FalkorDB")
class LiveFalkorDBTests(unittest.TestCase):
    def test_response_action_verifies_and_restores_original_synthetic_permission_state(self):
        alert_id = "BH-IAM-2001"
        with graph_connection() as graph:
            rows = graph.ro_query(
                "MATCH (:IAMRole {id: 'analytics-role'})-[r:CAN_ASSUME {assessment: 'EXCESSIVE'}]->(:IAMRole {id: 'production-role'}) RETURN r.response_status"
            ).result_set
        self.assertTrue(rows, "seeded excessive permission edge is required")
        original = rows[0][0]
        try:
            with graph_connection() as graph:
                graph.query(
                    "MATCH (:IAMRole {id: 'analytics-role'})-[r:CAN_ASSUME {assessment: 'EXCESSIVE'}]->(:IAMRole {id: 'production-role'}) SET r.response_status = 'ACTIVE' RETURN r.response_status"
                )
            candidates = response_candidates(alert_id)["items"]
            choice = next(item for item in candidates if item["action_type"] == "BLOCK_EXCESSIVE_ASSUMPTION")
            result = execute_response_action(alert_id, choice["action_id"], choice["target_id"])
            self.assertEqual(result["status"], "verified")
            self.assertEqual(result["after"]["outcome"], "no_path_in_graph")
            self.assertEqual(result["action"]["after_state"], "BLOCKED")
        finally:
            with graph_connection() as graph:
                if original is None:
                    graph.query(
                        "MATCH (:IAMRole {id: 'analytics-role'})-[r:CAN_ASSUME {assessment: 'EXCESSIVE'}]->(:IAMRole {id: 'production-role'}) REMOVE r.response_status RETURN r.assessment"
                    )
                else:
                    graph.query(
                        "MATCH (:IAMRole {id: 'analytics-role'})-[r:CAN_ASSUME {assessment: 'EXCESSIVE'}]->(:IAMRole {id: 'production-role'}) SET r.response_status = $state RETURN r.assessment",
                        {"state": original},
                    )
        self.assertEqual(integration.investigate_alert(alert_id).outcome, "path_found")

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

    def test_audit_fixture_is_idempotent_and_supports_bounded_depth_comparison(self):
        with graph_connection() as graph:
            seed_blastshield_graph(graph)
        before_canonical = {
            alert["id"]: integration.investigate_alert(alert["id"])
            for alert in integration.ALERT_CATALOG
        }
        with graph_connection() as graph:
            def fixture_counts():
                node_count = graph.ro_query(
                    "MATCH (n) WHERE n.id STARTS WITH 'auditor_' OR n.id STARTS WITH 'sess_auditor_' "
                    "OR n.id STARTS WITH 'audit-' OR n.id STARTS WITH 'staging-' "
                    "OR n.id STARTS WITH 'analytics-cache' RETURN count(n)"
                ).result_set[0][0]
                relationship_count = graph.ro_query(
                    "MATCH (a)-[r]->(b) WHERE (a.id STARTS WITH 'auditor_' OR a.id STARTS WITH 'sess_auditor_' "
                    "OR a.id STARTS WITH 'audit-' OR a.id STARTS WITH 'staging-' OR a.id = 'analytics-cache') "
                    "AND (b.id STARTS WITH 'auditor_' OR b.id STARTS WITH 'sess_auditor_' "
                    "OR b.id STARTS WITH 'audit-' OR b.id STARTS WITH 'staging-' OR b.id = 'analytics-cache') "
                    "RETURN count(r)"
                ).result_set[0][0]
                return node_count, relationship_count

            before_counts = fixture_counts()
            self.assertEqual(before_counts, (7, 7))
            seed_blastshield_graph(graph)
            seed_blastshield_graph(graph)
            after_counts = fixture_counts()
        self.assertEqual(after_counts, before_counts)

        shallow = graph_service.find_shortest_access_path("auditor_02", "staging-report", max_hops=4)
        deeper = graph_service.find_shortest_access_path("auditor_02", "staging-report", max_hops=5)
        self.assertEqual(shallow["status"], "no_path")
        self.assertIn("No qualifying path within the selected traversal depth", shallow["message"])
        self.assertEqual(deeper["status"], "path_found")
        self.assertEqual(deeper["hop_count"], 5)
        self.assertEqual(
            [edge["type"] for edge in deeper["path"]["edges"]],
            ["HAS_SESSION", "CAN_ACCESS", "RUNS_AS", "CAN_ASSUME", "CAN_READ"],
        )

        direct = graph_service.find_shortest_access_path("auditor_02", "analytics-cache", max_hops=5)
        self.assertEqual(direct["status"], "path_found")
        self.assertEqual(direct["hop_count"], 4)
        self.assertEqual(direct["path"]["edges"][-1]["target"], "analytics-cache")
        self.assertEqual(
            [edge["type"] for edge in direct["path"]["edges"]],
            ["HAS_SESSION", "CAN_ACCESS", "RUNS_AS", "CAN_READ"],
        )

        after_canonical = {
            alert["id"]: integration.investigate_alert(alert["id"])
            for alert in integration.ALERT_CATALOG
        }
        for alert_id, before in before_canonical.items():
            after = after_canonical[alert_id]
            self.assertEqual(after.outcome, before.outcome, alert_id)
            self.assertEqual(after.risk_assessment.score, before.risk_assessment.score, alert_id)
            self.assertEqual(
                [[node.id for node in path.nodes] for path in after.paths],
                [[node.id for node in path.nodes] for path in before.paths],
                alert_id,
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
