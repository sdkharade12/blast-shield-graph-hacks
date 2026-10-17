import unittest
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError

from app import integration, main
from app.schemas import InvestigationRequest, InvestigationResponse
from app.mcp import server as mcp_server


class IntegrationTests(unittest.TestCase):
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
        self.assertIsNone(result.risk_score)
        self.assertEqual(result.severity, "UNASSESSED")
        self.assertEqual(result.data_source, "synthetic_falkordb")
        self.assertEqual(result.paths[0].nodes[0].label, "exposed-api-01")
        self.assertEqual(result.paths[0].nodes[0].type, "Workload")
        self.assertEqual(result.paths[0].edges[0].properties, {"scope": "read"})
        self.assertTrue(any("asset_sensitivity=critical" in item for item in result.evidence))

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
        self.assertEqual(result["investigation"]["data_source"], "synthetic_falkordb")
        self.assertEqual(radius["path_count"], len(result["investigation"]["paths"]))


if __name__ == "__main__":
    unittest.main()
