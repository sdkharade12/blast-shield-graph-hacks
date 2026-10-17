import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from app import main
from app.agent import response_service
from app.integration import ALERT_CATALOG


def investigation(alert_id, edges, outcome="path_found"):
    target = next(item for item in ALERT_CATALOG if item["id"] == alert_id)
    nodes = [
        SimpleNamespace(id=target["target_id"], type=target["target_type"]),
        SimpleNamespace(id="session-1", type="Session"),
        SimpleNamespace(id="critical-db", type="DataAsset"),
    ]
    path = SimpleNamespace(nodes=nodes, edges=[SimpleNamespace(**edge) for edge in edges])
    return SimpleNamespace(
        outcome=outcome,
        paths=[path] if outcome == "path_found" else [],
        model_dump=lambda mode="json": {"alert_id": alert_id, "outcome": outcome, "paths": []},
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


if __name__ == "__main__":
    unittest.main()
