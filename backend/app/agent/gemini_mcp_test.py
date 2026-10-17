import asyncio
import os
import sys

from google import genai
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(PROJECT_ROOT / ".env")


async def main():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is missing from this terminal."
        )

    # Start our MCP server as a subprocess.
    # Run this script from the backend directory.
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.mcp.server"],
        env=None,
    )

    prompt = """
    Investigate alert BH-1042.

    Use investigate_alert to retrieve its context.
    Then use get_blast_radius with the same alert ID.

    Explain the represented paths, relevant entities, provenance,
    and limitations. State that no risk score is available if the
    service reports UNASSESSED.

    Base your explanation only on tool results. Identify the
    information as synthetic FalkorDB data. Do not claim successful
    exploitation or real-world containment.
    """

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            print("MCP tools available:")
            for tool in tools.tools:
                print(f"  - {tool.name}")

            async with genai.Client(api_key=api_key).aio as client:
                response = await client.models.generate_content(
                    model=os.getenv("GEMINI_MODEL", "gemini-flash-latest"),
                    contents=prompt,
                    config={
                        "tools": [session],
                    },
                )

                print("\nGemini's investigation:\n")
                print(response.text or "(No text returned)")

                # Show function-call history when provided by the SDK.
                history = (
                    getattr(
                        response,
                        "automatic_function_calling_history",
                        None,
                    )
                    or []
                )

                print("\nTool-call history:")
                if not history:
                    print(
                        "No tool-call history exposed in this response."
                    )

                for turn in history:
                    for part in getattr(turn, "parts", []) or []:
                        call = getattr(part, "function_call", None)
                        result = getattr(part, "function_response", None)

                        if call:
                            print(f"CALLED: {call.name}")
                            print(f"ARGUMENTS: {call.args}")

                        if result:
                            print(f"RESULT: {result.name}")
                            print(result.response)


if __name__ == "__main__":
    asyncio.run(main())
