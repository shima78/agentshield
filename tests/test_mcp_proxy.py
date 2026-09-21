"""End-to-end tests against a real downstream MCP server subprocess.

Uses examples/mcp_server.py (a small fake MCP server with echo/create_file/
delete_file tools) launched locally over stdio via the official MCP SDK.
No network access is involved.
"""

import pathlib
import sys

import pytest

from agentshield import AuthorizationEngine, Policy
from agentshield.mcp import (
    ApprovalResult,
    CallbackApprovalProvider,
    DownstreamConfig,
    DownstreamConnectionError,
    DownstreamMCPProxy,
    MCPGateway,
)
from agentshield.mcp.proxy import _resolve_env

EXAMPLES_DIR = pathlib.Path(__file__).resolve().parents[1] / "examples"
SERVER_SCRIPT = EXAMPLES_DIR / "mcp_server.py"


def downstream_config() -> DownstreamConfig:
    return DownstreamConfig(command=sys.executable, args=[str(SERVER_SCRIPT)])


DEMO_POLICY = Policy.from_dict(
    {
        "rules": [
            {"name": "allow-echo", "tool": "echo", "outcome": "allow", "risk": "low"},
            {
                "name": "review-create-file",
                "tool": "create_file",
                "outcome": "review",
                "risk": "medium",
            },
            {
                "name": "deny-delete-file",
                "tool": "delete_file",
                "outcome": "deny",
                "risk": "critical",
            },
        ]
    }
)


def make_gateway(**kwargs) -> MCPGateway:
    return MCPGateway(
        engine=AuthorizationEngine(DEMO_POLICY),
        downstream=DownstreamMCPProxy(downstream_config()),
        server_name="demo",
        **kwargs,
    )


# --- Discovery --------------------------------------------------------------


async def test_tools_are_discovered_through_the_gateway():
    gateway = make_gateway()
    async with gateway:
        tools = await gateway.list_tools()
    names = {tool.name for tool in tools}
    assert names == {"echo", "create_file", "delete_file"}


async def test_tool_schema_is_preserved_unmodified():
    gateway = make_gateway()
    async with gateway:
        tools = await gateway.list_tools()
    echo_tool = next(tool for tool in tools if tool.name == "echo")
    assert "message" in echo_tool.input_schema["properties"]


# --- ALLOW: forwarded, real round trip --------------------------------------


async def test_allow_reaches_the_real_downstream_server():
    gateway = make_gateway()
    async with gateway:
        result = await gateway.call_tool("echo", {"message": "hello"})
    assert result.decision.outcome.value == "allow"
    assert result.executed is True
    assert result.result.content[0].text == "hello"


# --- DENY: never reaches the real downstream server -------------------------


async def test_deny_never_reaches_the_real_downstream_server(tmp_path):
    # If this were mistakenly forwarded, the fake server would raise
    # FileNotFoundError for a file that was never created — proving the
    # call truly never left the gateway, not just that it "happened to
    # succeed".
    gateway = make_gateway()
    async with gateway:
        result = await gateway.call_tool("delete_file", {"name": "never-created.txt"})
    assert result.decision.outcome.value == "deny"
    assert result.executed is False
    assert result.result is None


# --- REVIEW: approved call actually executes downstream --------------------


async def test_review_approved_creates_file_on_real_downstream_server():
    sandbox = EXAMPLES_DIR / "sandbox"
    target = sandbox / "gateway_test_note.txt"
    target.unlink(missing_ok=True)

    approver = CallbackApprovalProvider(lambda request, decision: ApprovalResult(approved=True))
    gateway = make_gateway(approval_provider=approver)
    try:
        async with gateway:
            result = await gateway.call_tool(
                "create_file", {"name": "gateway_test_note.txt", "content": "hi"}
            )
        assert result.executed is True
        assert target.exists()
    finally:
        target.unlink(missing_ok=True)


# --- Unknown tool -----------------------------------------------------------


async def test_unknown_tool_is_a_transparent_error_result_not_an_exception():
    # The fake server reports "unknown tool" as a normal MCP error result
    # (CallToolResult.is_error=True), not a broken call. Per the downstream
    # transparency requirement, this must pass through unmodified rather
    # than being raised as a gateway exception.
    gateway = make_gateway()
    async with gateway:
        result = await gateway.call_tool("does_not_exist", {})
    assert result.decision.outcome.value == "allow"  # no rule matches -> default allow
    assert result.executed is True
    assert result.result.is_error is True


# --- Connection failure ------------------------------------------------------


async def test_downstream_unavailable_raises_connection_error():
    proxy = DownstreamMCPProxy(
        DownstreamConfig(command=sys.executable, args=["-c", "import sys; sys.exit(1)"])
    )
    with pytest.raises(DownstreamConnectionError):
        await proxy.connect()


async def test_proxy_methods_require_connection_first():
    proxy = DownstreamMCPProxy(downstream_config())
    with pytest.raises(DownstreamConnectionError):
        await proxy.list_tools()
    with pytest.raises(DownstreamConnectionError):
        await proxy.call_tool("echo", {"message": "hi"})


# --- Env var placeholder resolution (used for secrets like GitHub tokens) --
#
# These let a committed gateway config reference a secret by name
# (e.g. "${GITHUB_PERSONAL_ACCESS_TOKEN}") without ever containing its
# value; the value is only resolved from this process's environment right
# before the downstream subprocess is spawned.


def test_resolve_env_none_passes_through():
    assert _resolve_env(None) is None


def test_resolve_env_passes_through_literal_values():
    assert _resolve_env({"GITHUB_TOOLSETS": "repos,pull_requests"}) == {
        "GITHUB_TOOLSETS": "repos,pull_requests"
    }


def test_resolve_env_expands_placeholder_from_process_environment(monkeypatch):
    monkeypatch.setenv("AGENTSHIELD_TEST_TOKEN", "secret-value")
    resolved = _resolve_env({"GITHUB_PERSONAL_ACCESS_TOKEN": "${AGENTSHIELD_TEST_TOKEN}"})
    assert resolved == {"GITHUB_PERSONAL_ACCESS_TOKEN": "secret-value"}


def test_resolve_env_missing_placeholder_variable_raises_clearly(monkeypatch):
    monkeypatch.delenv("AGENTSHIELD_TEST_TOKEN_MISSING", raising=False)
    with pytest.raises(DownstreamConnectionError, match="AGENTSHIELD_TEST_TOKEN_MISSING"):
        _resolve_env({"GITHUB_PERSONAL_ACCESS_TOKEN": "${AGENTSHIELD_TEST_TOKEN_MISSING}"})
