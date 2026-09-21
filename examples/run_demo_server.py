"""Real end-to-end demo: a real MCP client -> the AgentShield MCP *server* ->
a fake downstream MCP server.

Unlike run_demo.py (which embeds ``MCPGateway`` directly as a library),
this launches AgentShield exactly the way a real MCP client such as Claude
Desktop or Cursor would use it: as a standalone subprocess speaking MCP
over stdio.

    MCP Client (this script)
          |
          | MCP / stdio
          v
    AgentShield MCP Server   (python -m agentshield.mcp.server)
          |
          v
      Policy Engine
          |
     +----+----+------+
     |         |      |
   ALLOW     REVIEW  DENY
     |         |      |
     v      (blocked: no    X
     |       approval
     |       provider wired
     |       via the CLI)
     v
Fake Downstream MCP Server (examples/mcp_server.py)

Run from the repository root, with the project installed including the
optional "mcp" extra:

    pip install -e ".[mcp]"
    python examples/run_demo_server.py

Entirely local: no network access, no API keys, no external services.
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

EXAMPLES_DIR = pathlib.Path(__file__).resolve().parent
REPO_ROOT = EXAMPLES_DIR.parent


async def main() -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "agentshield.mcp.server",
            "--config",
            str(EXAMPLES_DIR / "gateway.yaml"),
            "--log-level",
            "WARNING",
        ],
        cwd=str(REPO_ROOT),
    )

    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()

            tools = await session.list_tools()
            print(f"Discovered downstream tools: {[t.name for t in tools.tools]}\n")

            print("--- echo('hello from a real MCP client') -> expect ALLOW ---")
            allow_result = await session.call_tool(
                "echo", {"message": "hello from a real MCP client"}
            )
            print(f"is_error: {allow_result.is_error}")
            print(f"content:  {allow_result.content}\n")

            print("--- create_file('note.txt') -> expect REVIEW, blocked (no approval wired) ---")
            review_result = await session.call_tool(
                "create_file", {"name": "note.txt", "content": "hi"}
            )
            print(f"is_error: {review_result.is_error}")
            print(f"content:  {review_result.content}\n")

            print("--- delete_file('anything.txt') -> expect DENY ---")
            deny_result = await session.call_tool("delete_file", {"name": "anything.txt"})
            print(f"is_error: {deny_result.is_error}")
            print(f"content:  {deny_result.content}")


if __name__ == "__main__":
    asyncio.run(main())
