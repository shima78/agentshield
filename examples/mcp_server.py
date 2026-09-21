"""A tiny fake downstream MCP server used for AgentShield demos and tests.

Exposes three harmless tools: ``echo``, ``create_file``, ``delete_file``.
All file operations are confined to a ``sandbox`` directory created next to
this script, so running the demo never touches anything outside of it.

Run standalone for a quick manual check:

    python examples/mcp_server.py
"""

from __future__ import annotations

import pathlib

from mcp.server.mcpserver import MCPServer

SANDBOX = (pathlib.Path(__file__).parent / "sandbox").resolve()
SANDBOX.mkdir(exist_ok=True)

server = MCPServer("agentshield-demo-server")


def _sandboxed_path(name: str) -> pathlib.Path:
    path = (SANDBOX / name).resolve()
    if path != SANDBOX and SANDBOX not in path.parents:
        raise ValueError("Refusing to touch a path outside the sandbox directory.")
    return path


@server.tool()
def echo(message: str) -> str:
    """Echo back the given message."""
    return message


@server.tool()
def create_file(name: str, content: str = "") -> str:
    """Create a file with the given name and content inside the sandbox."""
    path = _sandboxed_path(name)
    path.write_text(content, encoding="utf-8")
    return f"created {path.name}"


@server.tool()
def delete_file(name: str) -> str:
    """Delete a file with the given name from the sandbox."""
    path = _sandboxed_path(name)
    if not path.exists():
        raise FileNotFoundError(f"{name} does not exist in the sandbox")
    path.unlink()
    return f"deleted {name}"


if __name__ == "__main__":
    server.run(transport="stdio")
