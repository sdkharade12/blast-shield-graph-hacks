"""Response-only mutation capability; never included in Gemini's tool set."""
from typing import Any
from mcp.server import MCPServer
from app.agent.response_service import execute_response_action as run_action

mcp = MCPServer("BlastShield Synthetic Response Action")


@mcp.tool()
def execute_simulated_response(alert_id: str, action_id: str, target_id: str, confirmed: bool) -> dict[str, Any]:
    """Apply one allow-listed synthetic graph action after the API's explicit confirmation."""
    if confirmed is not True:
        return {"status": "confirmation_required", "alert_id": alert_id}
    return run_action(alert_id, action_id, target_id)


if __name__ == "__main__":
    mcp.run()
