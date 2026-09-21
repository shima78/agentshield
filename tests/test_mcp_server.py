"""Real end-to-end tests for the upstream-facing AgentShield MCP server.

Two harnesses are used:

* An in-memory ``ClientSession``<->``Server`` pair (via the MCP SDK's
  ``create_client_server_memory_streams``) driving a real
  ``AgentShieldMCPServer`` in-process. The downstream leg is still a real
  subprocess (examples/mcp_server.py). This is used for the full
  ALLOW/REVIEW/DENY matrix, since it lets tests wire a
  ``CallbackApprovalProvider`` (the CLI launcher intentionally does not).

* A genuine 3-hop subprocess test: a real MCP client connects to
  ``python -m agentshield.mcp.server --config ...`` exactly as an external
  MCP client (Claude Desktop, Cursor, ...) would, and that process in turn
  launches the fake downstream server as its own subprocess. No network
  access is involved anywhere.
"""

import os
import pathlib
import sys
from contextlib import asynccontextmanager

import anyio
import pytest
import yaml
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.memory import create_client_server_memory_streams

from agentshield import DecisionEngine, Policy
from agentshield.mcp import (
    ApprovalResult,
    CallbackApprovalProvider,
    DownstreamConfig,
    DownstreamMCPProxy,
    MCPGateway,
)
from agentshield.mcp.server import AgentShieldMCPServer

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
EXAMPLES_DIR = REPO_ROOT / "examples"
SERVER_SCRIPT = EXAMPLES_DIR / "mcp_server.py"

DEMO_RULES = [
    {"name": "allow-echo", "tool": "echo", "outcome": "allow", "risk": "low"},
    {
        "name": "review-create-file",
        "tool": "create_file",
        "outcome": "review",
        "risk": "medium",
        "reason": "Creating files requires human approval.",
    },
    {
        "name": "deny-delete-file",
        "tool": "delete_file",
        "outcome": "deny",
        "risk": "critical",
        "reason": "Deleting files is blocked by default.",
    },
]


def make_gateway(**kwargs) -> MCPGateway:
    downstream = DownstreamMCPProxy(DownstreamConfig(command=sys.executable, args=[str(SERVER_SCRIPT)]))
    return MCPGateway(
        engine=DecisionEngine(Policy.from_dict({"rules": DEMO_RULES})),
        downstream=downstream,
        server_name="demo",
        **kwargs,
    )


@asynccontextmanager
async def connected_session(gateway: MCPGateway):
    """Wire a real ClientSession to a real AgentShieldMCPServer in-process.

    The downstream leg (``gateway``) is still a real subprocess; only the
    upstream leg is carried over in-memory streams instead of a second
    subprocess, so tests can supply an approval provider the CLI launcher
    deliberately does not.
    """
    server = AgentShieldMCPServer(gateway)
    await gateway.connect()
    try:
        async with create_client_server_memory_streams() as (client_streams, server_streams):
            client_read, client_write = client_streams
            server_read, server_write = server_streams
            init_options = server._server.create_initialization_options()

            async with anyio.create_task_group() as tg:
                tg.start_soon(server._server.run, server_read, server_write, init_options)
                async with ClientSession(client_read, client_write) as session:
                    await session.initialize()
                    yield session
                tg.cancel_scope.cancel()
    finally:
        await gateway.close()


# --- Initialization & discovery ---------------------------------------------


async def test_client_can_initialize_and_discover_tools():
    gateway = make_gateway()
    async with connected_session(gateway) as session:
        result = await session.list_tools()
    assert {t.name for t in result.tools} == {"echo", "create_file", "delete_file"}


async def test_tool_schema_is_preserved_through_the_server():
    gateway = make_gateway()
    async with connected_session(gateway) as session:
        result = await session.list_tools()
    echo_tool = next(t for t in result.tools if t.name == "echo")
    assert "message" in echo_tool.input_schema["properties"]


# --- ALLOW --------------------------------------------------------------


async def test_allow_reaches_downstream_and_returns_result():
    gateway = make_gateway()
    async with connected_session(gateway) as session:
        result = await session.call_tool("echo", {"message": "hello"})
    assert result.is_error is False
    assert result.content[0].text == "hello"


async def test_allow_records_audit_event():
    gateway = make_gateway()
    async with connected_session(gateway) as session:
        await session.call_tool("echo", {"message": "hello"})
    events = gateway.audit_log.events
    assert len(events) == 1
    assert events[0].decision.outcome.value == "allow"
    assert events[0].request.arguments == {"message": "hello"}


# --- DENY ------------------------------------------------------------------


async def test_deny_never_reaches_downstream():
    # If forwarded, the fake server would raise FileNotFoundError for a
    # file that was never created — proving the call genuinely never left
    # the gateway rather than "happening to succeed".
    gateway = make_gateway()
    async with connected_session(gateway) as session:
        result = await session.call_tool("delete_file", {"name": "never-created.txt"})
    assert result.is_error is True
    text = result.content[0].text
    assert "denied" in text.lower()
    assert "deny-delete-file" in text


async def test_deny_response_does_not_leak_internal_details():
    gateway = make_gateway()
    async with connected_session(gateway) as session:
        result = await session.call_tool("delete_file", {"name": "never-created.txt"})
    text = result.content[0].text
    assert "Traceback" not in text
    assert str(SERVER_SCRIPT) not in text
    assert sys.executable not in text


async def test_deny_records_audit_event():
    gateway = make_gateway()
    async with connected_session(gateway) as session:
        await session.call_tool("delete_file", {"name": "x"})
    event = gateway.audit_log.events[0]
    assert event.decision.outcome.value == "deny"
    assert event.approval_required is False


# --- REVIEW ------------------------------------------------------------


async def test_review_without_approval_provider_fails_closed_and_never_executes():
    gateway = make_gateway()  # no approval_provider configured
    async with connected_session(gateway) as session:
        result = await session.call_tool("create_file", {"name": "note.txt", "content": "hi"})
    assert result.is_error is True
    assert "approval" in result.content[0].text.lower()
    assert not (EXAMPLES_DIR / "sandbox" / "note.txt").exists()


async def test_review_approved_executes_downstream():
    approver = CallbackApprovalProvider(lambda request, decision: ApprovalResult(approved=True))
    gateway = make_gateway(approval_provider=approver)
    target = EXAMPLES_DIR / "sandbox" / "server_test_note.txt"
    target.unlink(missing_ok=True)
    try:
        async with connected_session(gateway) as session:
            result = await session.call_tool(
                "create_file", {"name": "server_test_note.txt", "content": "hi"}
            )
        assert result.is_error is False
        assert target.exists()
    finally:
        target.unlink(missing_ok=True)


async def test_review_rejected_does_not_execute():
    approver = CallbackApprovalProvider(lambda request, decision: ApprovalResult(approved=False))
    gateway = make_gateway(approval_provider=approver)
    async with connected_session(gateway) as session:
        result = await session.call_tool("create_file", {"name": "rejected.txt", "content": "hi"})
    assert result.is_error is True
    assert not (EXAMPLES_DIR / "sandbox" / "rejected.txt").exists()


async def test_review_records_approval_outcome_in_audit():
    approver = CallbackApprovalProvider(lambda request, decision: ApprovalResult(approved=True))
    gateway = make_gateway(approval_provider=approver)
    target = EXAMPLES_DIR / "sandbox" / "audit_note.txt"
    target.unlink(missing_ok=True)
    try:
        async with connected_session(gateway) as session:
            await session.call_tool("create_file", {"name": "audit_note.txt", "content": "hi"})
        event = gateway.audit_log.events[0]
        assert event.approval_required is True
        assert event.approval_outcome == "approved"
    finally:
        target.unlink(missing_ok=True)


# --- Arguments preserved -----------------------------------------------


async def test_arguments_arrive_downstream_unchanged():
    approver = CallbackApprovalProvider(lambda request, decision: ApprovalResult(approved=True))
    gateway = make_gateway(approval_provider=approver)
    target = EXAMPLES_DIR / "sandbox" / "exact_content.txt"
    target.unlink(missing_ok=True)
    try:
        async with connected_session(gateway) as session:
            await session.call_tool(
                "create_file", {"name": "exact_content.txt", "content": "exact-bytes-123"}
            )
        assert target.read_text(encoding="utf-8") == "exact-bytes-123"
    finally:
        target.unlink(missing_ok=True)


# --- Real 3-hop subprocess: client -> agentshield server -> fake server ----


def _write_gateway_config(tmp_path: pathlib.Path) -> pathlib.Path:
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(yaml.safe_dump({"rules": DEMO_RULES}), encoding="utf-8")

    config_path = tmp_path / "gateway.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "server": {"name": "demo"},
                "downstream": {"command": sys.executable, "args": [str(SERVER_SCRIPT)]},
                "policy": {"path": str(policy_path)},
            }
        ),
        encoding="utf-8",
    )
    return config_path


@asynccontextmanager
async def connected_cli_subprocess(config_path: pathlib.Path):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "agentshield.mcp.server", "--config", str(config_path)],
        env=env,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def test_real_subprocess_initialize_discover_allow_and_deny(tmp_path):
    config_path = _write_gateway_config(tmp_path)
    async with connected_cli_subprocess(config_path) as session:
        tools = await session.list_tools()
        assert {t.name for t in tools.tools} == {"echo", "create_file", "delete_file"}

        allow_result = await session.call_tool("echo", {"message": "hi"})
        assert allow_result.is_error is False
        assert allow_result.content[0].text == "hi"

        deny_result = await session.call_tool("delete_file", {"name": "never-created.txt"})
        assert deny_result.is_error is True
        assert "denied" in deny_result.content[0].text.lower()


async def test_real_subprocess_downstream_connection_failure_is_surfaced_cleanly(tmp_path):
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(yaml.safe_dump({"rules": []}), encoding="utf-8")
    config_path = tmp_path / "gateway.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "server": {"name": "demo"},
                "downstream": {"command": sys.executable, "args": ["-c", "import sys; sys.exit(1)"]},
                "policy": {"path": str(policy_path)},
            }
        ),
        encoding="utf-8",
    )

    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "agentshield.mcp.server", "--config", str(config_path)],
        env=env,
    )
    with pytest.raises(Exception):
        with anyio.fail_after(15):
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
