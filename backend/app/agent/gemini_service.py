
import os
import sys
from pathlib import Path
from dotenv import load_dotenv
from google import genai
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(PROJECT_ROOT / ".env")

BACKEND_DIR = Path(__file__).resolve().parents[2]

SYSTEM_INSTRUCTION = """
You are BlastShield, a cloud-security investigation agent.

Use the available MCP tools to investigate the supplied alert.
Base your findings only on the returned tool results.

Clearly distinguish potential access from confirmed exploitation.
Do not claim real-world containment has occurred.
The tools query a locally seeded synthetic FalkorDB graph, not a live cloud tenant.

If the alert or identity is unknown, say so.
If no qualifying path is returned, do not claim the environment
is safe.

Explain the findings, relevant entities, and limitations simply.
"""


async def run_gemini_investigation(alert_id: str) -> str:
    api_key = os.getenv("GEMINI_API_KEY")

    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set in the backend terminal."
        )

    # Ensure the spawned MCP process can import the app package,
    # regardless of the terminal's original PYTHONPATH.
    child_env = os.environ.copy()
    old_pythonpath = child_env.get("PYTHONPATH", "")

    child_env["PYTHONPATH"] = (
        str(BACKEND_DIR)
        + (os.pathsep + old_pythonpath if old_pythonpath else "")
    )

    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.mcp.server"],
        env=child_env,
    )

    prompt = f"""
Investigate security alert {alert_id}.

1. Call investigate_alert with this alert ID. Its response is the canonical structured investigation.
2. Call get_blast_radius with the same alert ID to summarize path-derived workloads, roles, and assets.
3. For the containment scenario, call get_containment_route_statuses with the same alert ID.
4. Summarise the findings using only the actual tool results and acknowledge their synthetic FalkorDB provenance.

Do not invent missing relationships or permissions.
Do not invent risk scores. If the service reports UNASSESSED, say no deterministic risk score is available.
Do not attempt containment or claim a route was changed; the tools are read-only.
"""

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            async with genai.Client(api_key=api_key).aio as client:
                model_name = os.getenv(
                    "GEMINI_MODEL",
                    "gemini-flash-latest",
                )
                print(f"[Gemini] FastAPI service using model: {model_name}")

                response = await client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config={
                        "tools": [session],
                        "system_instruction": SYSTEM_INSTRUCTION,
                        "temperature": 0.2,
                    },
                )

                answer = response.text
                if not answer:
                    raise RuntimeError(
                        "Gemini returned no text explanation."
                    )

                return answer
