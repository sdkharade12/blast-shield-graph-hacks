import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError

from app import integration, main
from app.agent import gemini_service
from app.schemas import InvestigationRequest, InvestigationResponse
from app.mcp import server as mcp_server
from app.services import graph_service
from app.graph.queries import FIND_SHORTEST_ACCESS_PATH_QUERY


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        mcp_server.TOOL_TRACE.clear()

    def test_catalog_has_one_entry_per_graph_scenario(self):
        self.assertEqual(
            [alert["scenario"] for alert in integration.ALERT_CATALOG],
            [
                "identity_investigation",
                "excessive_iam_permissions",
                "exposed_workload_blast_radius",
                "containment_verification",
            ],
        )
        self.assertIsNone(integration.find_alert("not-an-alert"))

    def test_all_scenario_alerts_dispatch_to_their_graph_targets(self):
        calls = []

        def investigator(target_id):
            calls.append(target_id)
            return {"status": "no_path", "paths": [], "evidence": [], "error": None}

        replacements = {scenario: investigator for scenario in integration._INVESTIGATORS}
        with patch.object(integration, "_INVESTIGATORS", replacements):
            results = [integration.investigate_alert(alert["id"]) for alert in integration.ALERT_CATALOG]

        self.assertEqual(calls, ["developer_07", "analytics-role", "exposed-api-01", "containment-user-01"])
        self.assertTrue(all(result.outcome == "no_path_in_graph" for result in results))

    def test_blank_alert_id_is_rejected(self):
        with self.assertRaises(ValidationError):
            InvestigationRequest(alert_id="   ")

    def test_scenario_dispatch_normalizes_real_graph_contract(self):
        calls = []

        def fake_service(target_id):
            calls.append(target_id)
            return {
                "status": "path_found",
                "paths": [
                    {
                        "nodes": [
                            {"id": target_id, "label": "Workload"},
                            {"id": "payments-db", "label": "DataAsset"},
                        ],
                        "edges": [
                            {
                                "source": target_id,
                                "target": "payments-db",
                                "type": "CAN_READ",
                                "properties": {"scope": "read"},
                            }
                        ],
                    }
                ],
                "evidence": [
                    {
                        "node_ids": [target_id, "payments-db"],
                        "relationship_types": ["CAN_READ"],
                        "additional_evidence": {"asset_sensitivity": "critical"},
                    }
                ],
                "error": None,
            }

        with patch.object(
            integration,
            "_INVESTIGATORS",
            {**integration._INVESTIGATORS, "exposed_workload_blast_radius": fake_service},
        ):
            result = integration.investigate_alert("BH-WORKLOAD-3001")

        self.assertEqual(calls, ["exposed-api-01"])
        self.assertEqual(result.outcome, "path_found")
        self.assertEqual(result.target, {"type": "Workload", "id": "exposed-api-01"})
        self.assertIsNone(result.identity_id)
        self.assertEqual(result.risk_score, 65)
        self.assertEqual(result.severity, "HIGH")
        self.assertEqual(result.risk_assessment.rule_version, "demo-rules-v1")
        self.assertEqual(result.trace[0].result, "path_found")
        self.assertEqual(result.data_source, "synthetic_falkordb")
        self.assertEqual(result.paths[0].nodes[0].label, "exposed-api-01")
        self.assertEqual(result.paths[0].nodes[0].type, "Workload")
        self.assertEqual(result.paths[0].edges[0].properties, {"scope": "read"})
        self.assertTrue(any("asset_sensitivity=critical" in item for item in result.evidence))
        self.assertEqual(result.trace[0].evidence[0], "exposed-api-01")

    def test_no_path_is_not_unknown_or_query_error(self):
        with patch.object(
            integration,
            "_INVESTIGATORS",
            {**integration._INVESTIGATORS, "excessive_iam_permissions": lambda _target: {
                "status": "no_path",
                "paths": [],
                "evidence": [],
                "error": None,
            }},
        ):
            result = integration.investigate_alert("BH-IAM-2001")
        self.assertEqual(result.outcome, "no_path_in_graph")
        self.assertEqual(result.paths, [])

    def test_demo_rules_match_the_canonical_seeded_scenarios(self):
        path = {"nodes": [], "edges": []}
        cases = [
            ("identity_investigation", [path], 35, "MEDIUM"),
            ("excessive_iam_permissions", [path], 65, "HIGH"),
            ("exposed_workload_blast_radius", [path, path, path], 75, "HIGH"),
            ("containment_verification", [path, path], 60, "HIGH"),
        ]
        for scenario, paths, expected_score, expected_severity in cases:
            with self.subTest(scenario=scenario):
                assessment = integration._assess_risk(scenario, "path_found", paths)
                self.assertEqual(assessment["score"], expected_score)
                self.assertEqual(assessment["severity"], expected_severity)
                self.assertEqual(assessment["rule_version"], "demo-rules-v1")

    def test_unknown_entity_and_query_error_remain_distinct(self):
        with patch.object(
            integration,
            "_INVESTIGATORS",
            {**integration._INVESTIGATORS, "identity_investigation": lambda _target: {
                "status": "unknown_identity", "paths": [], "evidence": [], "error": None
            }},
        ):
            unknown = integration.investigate_alert("BH-1042")
        with patch.object(
            integration,
            "_INVESTIGATORS",
            {**integration._INVESTIGATORS, "identity_investigation": lambda _target: {
                "status": "query_error", "paths": [], "evidence": [], "error": "Graph query failed."
            }},
        ):
            failed = integration.investigate_alert("BH-1042")
        self.assertEqual(unknown.outcome, "unknown_entity")
        self.assertEqual(failed.outcome, "query_error")

    def test_structured_and_agent_routes_remain_separate(self):
        fake = InvestigationResponse(
            investigation_id="INV-BH-1042",
            alert_id="BH-1042",
            target={"type": "Identity", "id": "developer_07"},
            identity_id="developer_07",
            outcome="path_found",
            risk_score=None,
            severity="UNASSESSED",
            summary="Potential path in synthetic graph.",
            paths=[],
            evidence=[],
            proposed_actions=[],
            warnings=[],
        )
        with patch.object(main, "investigate_alert", lambda _alert_id: fake):
            structured = main.investigate(InvestigationRequest(alert_id="BH-1042"))
        self.assertEqual(structured.data_source, "synthetic_falkordb")
        self.assertIsInstance(structured, InvestigationResponse)
        self.assertNotIn("answer", structured.model_dump())
        with self.assertRaises(HTTPException) as raised:
            main.investigate(InvestigationRequest(alert_id="unknown"))
        self.assertEqual(raised.exception.status_code, 404)

        route_endpoints = {
            route.path: route.endpoint
            for route in main.app.routes
            if getattr(route, "path", None) in {
                "/api/investigations", "/api/agent/investigate"
            }
        }
        self.assertIsNot(route_endpoints["/api/investigations"], route_endpoints["/api/agent/investigate"])

    def test_containment_requires_confirmation(self):
        calls = []
        with patch.object(
            main,
            "simulate_containment",
            lambda alert_id, route_id: calls.append((alert_id, route_id))
            or {
                "status": "contained",
                "route_id": route_id,
                "containment_status": "BLOCKED",
            },
        ), patch.object(
            main,
            "get_containment_status",
            lambda _alert_id: {"status": "routes_found", "routes": []},
        ):
            with self.assertRaises(HTTPException) as denied:
                main.containment_simulate(
                    main.ContainmentSimulationRequest(
                        alert_id="BH-CONTAINMENT-4001",
                        route_id="primary",
                        confirmed=False,
                    )
                )
            accepted = main.containment_simulate(
                main.ContainmentSimulationRequest(
                    alert_id="BH-CONTAINMENT-4001",
                    route_id="primary",
                    confirmed=True,
                )
            )

        self.assertEqual(denied.exception.status_code, 400)
        self.assertEqual(accepted["status"], "contained")
        self.assertEqual(calls, [("BH-CONTAINMENT-4001", "primary")])

    def test_mcp_tools_share_canonical_dispatch(self):
        unknown = mcp_server.investigate_alert("unknown")
        self.assertEqual(unknown["status"], "unknown_alert")
        self.assertEqual(mcp_server.get_blast_radius("unknown")["status"], "unknown_alert")

        fake = InvestigationResponse(
            investigation_id="INV-BH-1042",
            alert_id="BH-1042",
            target={"type": "Identity", "id": "developer_07"},
            identity_id="developer_07",
            outcome="path_found",
            risk_score=None,
            severity="UNASSESSED",
            summary="Potential path.",
            paths=[],
            evidence=[],
            proposed_actions=[],
            warnings=["synthetic"],
        )
        with patch.object(mcp_server, "run_investigation", lambda _alert_id: fake):
            result = mcp_server.investigate_alert("BH-1042")
            radius = mcp_server.get_blast_radius("BH-1042")
        self.assertEqual(result["status"], "path_found")
        self.assertEqual(result["investigation"]["data_source"], "synthetic_falkordb")
        self.assertEqual(radius["path_count"], len(result["investigation"]["paths"]))

    def test_mcp_propagates_canonical_non_finding_outcomes(self):
        fake = InvestigationResponse(
            investigation_id="INV-BH-1042",
            alert_id="BH-1042",
            target={"type": "Identity", "id": "developer_07"},
            outcome="no_path_in_graph",
            risk_score=None,
            severity="UNASSESSED",
            summary="No path.",
            paths=[], evidence=[], proposed_actions=[],
        )
        with patch.object(mcp_server, "run_investigation", lambda _alert_id: fake):
            result = mcp_server.investigate_alert("BH-1042")
        self.assertEqual(result["status"], "no_path_in_graph")

    def test_mcp_records_actual_tool_calls_and_trace_can_be_read(self):
        with patch.object(mcp_server, "run_investigation", return_value=None):
            # A known alert dispatch that fails to produce a canonical result is query_error.
            response = mcp_server.investigate_alert("BH-1042")
        trace = mcp_server.get_tool_trace()["items"]
        self.assertEqual(response["status"], "query_error")
        self.assertEqual(trace[0]["tool_name"], "investigate_alert")
        self.assertEqual(trace[0]["input"], {"alert_id": "BH-1042"})
        self.assertGreaterEqual(trace[0]["duration_ms"], 0)

    def test_mcp_route_status_trace_preserves_route_states(self):
        with patch.object(
            mcp_server,
            "get_containment_status",
            return_value={
                "status": "routes_found",
                "routes": [
                    {"route_id": "primary", "containment_status": "ACTIVE"},
                    {"route_id": "alternate", "containment_status": "BLOCKED"},
                ],
            },
        ):
            mcp_server.get_containment_route_statuses("BH-CONTAINMENT-4001")
        trace = mcp_server.get_tool_trace()["items"]
        self.assertEqual(trace[0]["result_count"], 2)
        self.assertEqual(
            trace[0]["route_statuses"],
            [
                {"route_id": "primary", "containment_status": "ACTIVE"},
                {"route_id": "alternate", "containment_status": "BLOCKED"},
            ],
        )

    def test_shortest_path_query_is_depth_and_relationship_bounded(self):
        self.assertIn("*1..6", FIND_SHORTEST_ACCESS_PATH_QUERY)
        for relationship in ("HAS_SESSION", "CAN_ACCESS", "RUNS_AS", "CAN_ASSUME", "CAN_READ"):
            self.assertIn(relationship, FIND_SHORTEST_ACCESS_PATH_QUERY)
        self.assertIn("all(rel IN relationships(p)", FIND_SHORTEST_ACCESS_PATH_QUERY)

    def _mock_shortest_query(self, source, target, paths):
        graph = SimpleNamespace()

        def ro_query(query, params=None):
            if "MATCH (n {id: $entity_id})" in query:
                entity_id = params["entity_id"]
                row = source if entity_id == "source" else target
                return SimpleNamespace(result_set=[row] if row else [])
            return SimpleNamespace(result_set=paths)

        graph.ro_query = ro_query
        return patch.object(graph_service, "graph_connection", lambda: nullcontext(graph))

    def test_shortest_path_distinguishes_unknown_source_and_target(self):
        with self._mock_shortest_query(None, ("target", "Target", "DataAsset"), []):
            unknown_source = graph_service.find_shortest_access_path("source", "target")
        with self._mock_shortest_query(("source", "Source", "Identity"), None, []):
            unknown_target = graph_service.find_shortest_access_path("source", "target")
        self.assertEqual(unknown_source["status"], "unknown_source")
        self.assertEqual(unknown_target["status"], "unknown_target")

    def test_shortest_path_returns_ordered_graph_evidence_and_no_path(self):
        source = ("source", "Auditor", "Identity")
        target = ("target", "Audit report", "DataAsset")
        node_data = [
            {"id": "source", "label": "Auditor", "type": "Identity"},
            {"id": "target", "label": "Audit report", "type": "DataAsset"},
        ]
        edge_data = [{"source": "source", "target": "target", "type": "CAN_READ", "properties": {"scope": "audit"}}]
        with self._mock_shortest_query(source, target, [(node_data, edge_data, 1)]):
            found = graph_service.find_shortest_access_path("source", "target")
        with self._mock_shortest_query(source, target, []):
            missing = graph_service.find_shortest_access_path("source", "target")
        self.assertEqual(found["status"], "path_found")
        self.assertEqual(found["hop_count"], 1)
        self.assertEqual([node["id"] for node in found["path"]["nodes"]], ["source", "target"])
        self.assertEqual(found["path"]["edges"][0]["properties"], {"scope": "audit"})
        self.assertEqual(missing["status"], "no_path")
        self.assertEqual(missing["max_hops"], 6)

    def test_grounding_accepts_only_canonical_score_and_relationship_claims(self):
        fake = InvestigationResponse(
            investigation_id="INV-BH-WORKLOAD-3001",
            alert_id="BH-WORKLOAD-3001",
            target={"type": "Workload", "id": "exposed-api-01"},
            outcome="path_found", risk_score=75, severity="HIGH",
            summary="Path found.",
            paths=[{
                "nodes": [
                    {"id": "exposed-api-01", "label": "API", "type": "Workload"},
                    {"id": "api-service-role", "label": "Role", "type": "IAMRole"},
                    {"id": "customer-db", "label": "Database", "type": "DataAsset"},
                ],
                "edges": [
                    {"source": "exposed-api-01", "target": "api-service-role", "type": "RUNS_AS"},
                    {"source": "api-service-role", "target": "customer-db", "type": "CAN_READ"},
                ],
            }, {
                "nodes": [
                    {"id": "exposed-api-01", "label": "API", "type": "Workload"},
                    {"id": "api-service-role", "label": "Role", "type": "IAMRole"},
                    {"id": "payments-db", "label": "Payments", "type": "DataAsset"},
                ],
                "edges": [
                    {"source": "exposed-api-01", "target": "api-service-role", "type": "RUNS_AS"},
                    {"source": "api-service-role", "target": "payments-db", "type": "CAN_READ"},
                ],
            }, {
                "nodes": [
                    {"id": "exposed-api-01", "label": "API", "type": "Workload"},
                    {"id": "api-service-role", "label": "Role", "type": "IAMRole"},
                    {"id": "config-store", "label": "Config", "type": "DataAsset"},
                ],
                "edges": [
                    {"source": "exposed-api-01", "target": "api-service-role", "type": "RUNS_AS"},
                    {"source": "api-service-role", "target": "config-store", "type": "CAN_READ"},
                ],
            }], evidence=[], proposed_actions=[], warnings=[],
            provenance={"dataset_version": "blastshield-synthetic-v1"},
        )
        with patch.object(gemini_service, "investigate_alert", return_value=fake):
            accepted, accepted_check = gemini_service._ground_answer(
                "Alert BH-WORKLOAD-3001 (INV-BH-WORKLOAD-3001) in blastshield-synthetic-v1: "
                "the represented paths use RUNS_AS and CAN_READ. Risk score: 75/100.",
                "BH-WORKLOAD-3001",
            )
            fallback, failed_check = gemini_service._ground_answer(
                "The path uses CAN_ASSUME and has a risk score of 99/100.",
                "BH-WORKLOAD-3001",
            )
        self.assertIn("represented path", accepted)
        self.assertEqual(accepted_check["status"], "grounded")
        self.assertIn("Canonical investigation", fallback)
        self.assertEqual(failed_check["status"], "fallback")

    def test_containment_verification_uses_post_action_graph_query(self):
        before = {"status": "path_found", "paths": [{"nodes": []}], "error": None}
        after = {"status": "no_path", "paths": [], "error": None}
        with patch.object(
            integration.graph_service,
            "find_remaining_containment_paths",
            side_effect=[before, after],
        ), patch.object(
            integration.graph_service,
            "simulate_containment",
            return_value={"status": "contained", "containment_status": "BLOCKED"},
        ), patch.object(
            integration.graph_service,
            "get_containment_route_statuses",
            return_value={"status": "routes_found", "routes": []},
        ):
            result = integration.simulate_containment("BH-CONTAINMENT-4001", "primary")
        self.assertEqual(result["verification_outcome"], "no_qualifying_path_in_modeled_graph")
        self.assertEqual(result["before"], before)
        self.assertEqual(result["after"], after)
        self.assertTrue(result["action"]["action_id"].startswith("SIM-"))
        self.assertEqual(result["action"]["previous_status"], "ACTIVE")

    def test_containment_post_action_query_error_is_not_success(self):
        with patch.object(
            integration.graph_service,
            "find_remaining_containment_paths",
            side_effect=[{"status": "path_found", "paths": [], "error": None},
                         {"status": "query_error", "paths": [], "error": "offline"}],
        ), patch.object(
            integration.graph_service,
            "simulate_containment",
            return_value={"status": "contained", "containment_status": "BLOCKED"},
        ), patch.object(
            integration.graph_service,
            "get_containment_route_statuses",
            return_value={"status": "routes_found", "routes": []},
        ):
            result = integration.simulate_containment("BH-CONTAINMENT-4001", "primary")
        self.assertEqual(result["status"], "contained")
        self.assertEqual(result["verification_outcome"], "verification_failed")
        self.assertEqual(result["verification_error"], "offline")


if __name__ == "__main__":
    unittest.main()
