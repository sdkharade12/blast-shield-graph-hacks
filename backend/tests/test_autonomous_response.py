import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from app import main
from app.agent import response_service
from app.agent import response_agent
from app.integration import ALERT_CATALOG


def investigation(alert_id, edges, outcome="path_found"):
    target = next(item for item in ALERT_CATALOG if item["id"] == alert_id)
    nodes = [
        SimpleNamespace(id=target["target_id"], type=target["target_type"]),
        SimpleNamespace(id="session-1", type="Session"),
        SimpleNamespace(id="critical-db", type="DataAsset"),
    ]
    path = SimpleNamespace(nodes=nodes, edges=[SimpleNamespace(**edge) for edge in edges])
    paths = [path] if outcome == "path_found" else []

    def model_dump(mode="json"):
        serialized_paths = [
            {
                "nodes": [
                    {
                        "id": node.id,
                        "label": getattr(node, "label", node.id),
                        "type": node.type,
                    }
                    for node in item.nodes
                ],
                "edges": [
                    {
                        "source": edge.source,
                        "target": edge.target,
                        "type": edge.type,
                        "properties": edge.properties,
                    }
                    for edge in item.edges
                ],
            }
            for item in paths
        ]
        return {
            "investigation_id": f"INV-{alert_id}",
            "alert_id": alert_id,
            "target": {"id": target["target_id"], "type": target["target_type"]},
            "identity_id": None,
            "outcome": outcome,
            "risk_score": None,
            "severity": "UNASSESSED",
            "summary": f"Fixture result: {outcome}",
            "paths": serialized_paths,
            "evidence": [],
            "proposed_actions": [],
            "warnings": [],
            "trace": [],
            "risk_assessment": None,
            "provenance": {},
            "data_source": "synthetic_falkordb",
        }

    return SimpleNamespace(
        outcome=outcome,
        paths=paths,
        model_dump=model_dump,
    )


class AutonomousResponseTests(unittest.TestCase):
    def test_session_candidate_is_derived_from_returned_has_session_edge(self):
        alert_id = "BH-1042"
        edge = {"source": "developer_07", "target": "sess_007", "type": "HAS_SESSION", "properties": {"response_status": "ACTIVE"}}
        with patch.object(response_service, "investigate_alert", return_value=investigation(alert_id, [edge])):
            result = response_service.response_candidates(alert_id)
        self.assertEqual(result["items"][0]["action_type"], "BLOCK_SESSION")
        self.assertEqual(result["items"][0]["target_id"], "sess_007")

    def test_excessive_assumption_only_candidate_is_offered(self):
        alert_id = "BH-IAM-2001"
        edges = [
            {"source": "analytics-role", "target": "production-role", "type": "CAN_ASSUME", "properties": {"assessment": "EXCESSIVE", "response_status": "ACTIVE"}},
            {"source": "production-role", "target": "customer-db", "type": "CAN_READ", "properties": {}},
        ]
        with patch.object(response_service, "investigate_alert", return_value=investigation(alert_id, edges)):
            result = response_service.response_candidates(alert_id)
        self.assertEqual([item["action_type"] for item in result["items"]], ["BLOCK_EXCESSIVE_ASSUMPTION"])

    def test_confirmation_is_required_before_controller_can_run(self):
        request = main.AutonomousResponseRequest(alert_id="BH-1042", confirmed=False, max_actions=3)
        with patch("app.integration.find_alert", return_value=ALERT_CATALOG[0]), patch.object(main, "run_autonomous_response") as run:
            with self.assertRaises(HTTPException) as error:
                import asyncio
                asyncio.run(main.autonomous_response(request))
        self.assertEqual(error.exception.status_code, 400)
        run.assert_not_awaited()

    def test_api_limits_action_budget_to_three(self):
        from pydantic import ValidationError
        with self.assertRaises(ValidationError):
            main.AutonomousResponseRequest(alert_id="BH-1042", confirmed=True, max_actions=4)


class AutonomousResponseLoopTests(unittest.IsolatedAsyncioTestCase):
    def _decision(self, action_id, target_id):
        return {
            "action_id": action_id,
            "action_type": "BLOCK_CONTAINMENT_ROUTE",
            "target_id": target_id,
            "source_id": "containment-user-01",
            "rationale": f"Block graph-returned route {target_id}",
        }

    def _verified_action(self, target_id):
        return {
            "status": "verified",
            "action": {
                "action_id": f"block-route-{target_id}",
                "target_id": target_id,
                "before_state": "ACTIVE",
                "after_state": "BLOCKED",
                "run_action_id": f"ACT-{target_id}",
                "timestamp": "2026-10-17T12:00:00+00:00",
            },
            "verification": {"target_action_absent": True},
        }

    async def test_requeries_between_actions_and_finishes_only_after_no_path(self):
        alert_id = "BH-CONTAINMENT-4001"
        initial = investigation(alert_id, [{"source": "containment-user-01", "target": "primary-session", "type": "HAS_SESSION", "properties": {"route_id": "primary"}}])
        alternate_remains = investigation(alert_id, [{"source": "containment-user-01", "target": "alternate-session", "type": "HAS_SESSION", "properties": {"route_id": "alternate"}}])
        no_paths = investigation(alert_id, [], outcome="no_path_in_graph")
        choices = [self._decision("block-route-primary", "primary"), self._decision("block-route-alternate", "alternate")]
        with patch.object(response_agent, "find_alert", return_value=ALERT_CATALOG[3]), \
             patch.object(response_agent, "investigate_alert", side_effect=[initial, alternate_remains, no_paths]) as investigate, \
             patch.object(response_agent, "_ask_for_one_action", side_effect=[(choices[0], [], None), (choices[1], [], None)]) as choose, \
             patch.object(response_agent, "_invoke_response_action", side_effect=[(self._verified_action("primary"), 1), (self._verified_action("alternate"), 1)]) as execute:
            result = await response_agent.run_autonomous_response(alert_id, max_actions=3)

        self.assertEqual(choose.await_count, 2)
        self.assertEqual(execute.await_args_list[0].args[1:3], ("block-route-primary", "primary"))
        self.assertEqual(execute.await_args_list[1].args[1:3], ("block-route-alternate", "alternate"))
        self.assertEqual(investigate.call_count, 3)
        self.assertEqual(
            [
                call.args[1]["paths"][0]["edges"][0]["properties"]["route_id"]
                for call in choose.await_args_list
            ],
            ["primary", "alternate"],
            "each decision must receive the latest graph-derived route evidence",
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["final_outcome"], "no_qualifying_path")
        self.assertEqual(result["final_investigation"]["outcome"], "no_path_in_graph")

    async def test_action_limit_with_remaining_path_is_stopped_not_completed(self):
        alert_id = "BH-CONTAINMENT-4001"
        initial = investigation(alert_id, [{"source": "containment-user-01", "target": "primary-session", "type": "HAS_SESSION", "properties": {"route_id": "primary"}}])
        alternate_remains = investigation(alert_id, [{"source": "containment-user-01", "target": "alternate-session", "type": "HAS_SESSION", "properties": {"route_id": "alternate"}}])
        with patch.object(response_agent, "find_alert", return_value=ALERT_CATALOG[3]), \
             patch.object(response_agent, "investigate_alert", side_effect=[initial, alternate_remains]), \
             patch.object(response_agent, "_ask_for_one_action", return_value=(self._decision("block-route-primary", "primary"), [], None)), \
             patch.object(response_agent, "_invoke_response_action", return_value=(self._verified_action("primary"), 1)):
            result = await response_agent.run_autonomous_response(alert_id, max_actions=1)

        self.assertEqual(result["status"], "stopped")
        self.assertEqual(result["final_outcome"], "action_limit_reached")
        self.assertEqual(result["final_investigation"]["outcome"], "path_found")

    async def test_unknown_or_invalid_outcome_is_not_normalized_to_no_path(self):
        alert_id = "BH-CONTAINMENT-4001"
        for outcome in ("unknown_entity", "invalid_input"):
            with self.subTest(outcome=outcome):
                current = investigation(alert_id, [], outcome=outcome)
                with patch.object(response_agent, "find_alert", return_value=ALERT_CATALOG[3]), \
                     patch.object(response_agent, "investigate_alert", return_value=current), \
                     patch.object(response_agent, "_ask_for_one_action") as choose:
                    result = await response_agent.run_autonomous_response(alert_id, max_actions=3)

                choose.assert_not_awaited()
                self.assertEqual(result["status"], "stopped")
                self.assertEqual(result["final_outcome"], outcome)

    async def test_verification_failure_stops_before_selecting_another_action(self):
        alert_id = "BH-CONTAINMENT-4001"
        initial = investigation(alert_id, [{"source": "containment-user-01", "target": "primary-session", "type": "HAS_SESSION", "properties": {"route_id": "primary"}}])
        still_active = investigation(alert_id, [{"source": "containment-user-01", "target": "primary-session", "type": "HAS_SESSION", "properties": {"route_id": "primary"}}])
        failed_action = {"status": "verification_failed", "verification": {"target_action_absent": False}}
        with patch.object(response_agent, "find_alert", return_value=ALERT_CATALOG[3]), \
             patch.object(response_agent, "investigate_alert", side_effect=[initial, still_active]), \
             patch.object(response_agent, "_ask_for_one_action", return_value=(self._decision("block-route-primary", "primary"), [], None)) as choose, \
             patch.object(response_agent, "_invoke_response_action", return_value=(failed_action, 1)):
            result = await response_agent.run_autonomous_response(alert_id, max_actions=3)

        self.assertEqual(choose.await_count, 1)
        self.assertEqual(result["status"], "stopped")
        self.assertEqual(result["final_outcome"], "verification_failed")
        self.assertEqual(result["final_investigation"]["outcome"], "path_found")


if __name__ == "__main__":
    unittest.main()
