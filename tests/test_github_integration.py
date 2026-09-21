"""Real external integration tests against the real GitHub MCP server.

Unlike every other test file here (unit tests, and local end-to-end tests
against the fake bundled MCP server), these tests talk to a real, live
downstream MCP server (github/github-mcp-server, launched via Docker) and
the real GitHub API. They require:

* Docker, running, with network access to pull/run
  ``ghcr.io/github/github-mcp-server``.
* ``GITHUB_PERSONAL_ACCESS_TOKEN`` set in this process's environment.

Both are checked up front; if either is missing, every test in this module
is skipped with a clear reason. Plain `pytest` (no marker filter) therefore
still passes completely offline and credential-free, exactly like the rest
of the suite. To run these deliberately:

    pytest -m integration

To exclude them explicitly (e.g. in CI):

    pytest -m "not integration"

See examples/github/README.md for full setup instructions.
"""

from __future__ import annotations

import os
import pathlib
import shutil

import pytest

from agentshield.mcp import GatewayConfig, MCPGateway

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
GATEWAY_CONFIG_PATH = REPO_ROOT / "examples" / "github" / "gateway.yaml"

GITHUB_TOKEN_ENV = "GITHUB_PERSONAL_ACCESS_TOKEN"

# A long-lived, GitHub-owned demo repository, used only for a single
# read-only get_file_contents call. Overridable for anyone who'd rather
# point this at their own repository.
TEST_OWNER = os.environ.get("AGENTSHIELD_TEST_GITHUB_OWNER", "octocat")
TEST_REPO = os.environ.get("AGENTSHIELD_TEST_GITHUB_REPO", "Hello-World")
TEST_PATH = os.environ.get("AGENTSHIELD_TEST_GITHUB_PATH", "README")


def _docker_available() -> bool:
    return shutil.which("docker") is not None


def _prerequisites_missing_reason() -> str | None:
    if not os.environ.get(GITHUB_TOKEN_ENV):
        return f"{GITHUB_TOKEN_ENV} is not set."
    if not _docker_available():
        return "docker is not available on PATH."
    if not GATEWAY_CONFIG_PATH.exists():
        return f"{GATEWAY_CONFIG_PATH} not found."
    return None


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        _prerequisites_missing_reason() is not None,
        reason=_prerequisites_missing_reason() or "",
    ),
]


def make_gateway() -> MCPGateway:
    config = GatewayConfig.from_yaml(str(GATEWAY_CONFIG_PATH))
    return MCPGateway.from_config(config)


async def test_can_connect_and_discover_real_github_tools():
    gateway = make_gateway()
    async with gateway:
        tools = await gateway.list_tools()
    names = {tool.name for tool in tools}
    assert "get_file_contents" in names
    assert "delete_repository" in names


async def test_real_tool_schema_is_preserved():
    gateway = make_gateway()
    async with gateway:
        tools = await gateway.list_tools()
    get_file_contents = next(t for t in tools if t.name == "get_file_contents")
    schema_properties = get_file_contents.input_schema.get("properties", {})
    assert "owner" in schema_properties
    assert "repo" in schema_properties
    assert "path" in schema_properties


async def test_allowed_real_operation_reaches_github_and_is_audited():
    gateway = make_gateway()
    async with gateway:
        result = await gateway.call_tool(
            "get_file_contents",
            {"owner": TEST_OWNER, "repo": TEST_REPO, "path": TEST_PATH},
        )
    assert result.decision.outcome.value == "allow"
    assert result.executed is True
    assert result.result is not None

    events = gateway.audit_log.events
    assert len(events) == 1
    event = events[0]
    assert event.request.action == "get_file_contents"
    assert event.request.server == "github"
    assert event.decision.outcome.value == "allow"


async def test_protected_operation_is_blocked_before_reaching_github():
    gateway = make_gateway()
    async with gateway:
        result = await gateway.call_tool(
            "delete_repository",
            {"owner": TEST_OWNER, "repo": "this-repo-must-never-be-touched"},
        )
    assert result.decision.outcome.value == "deny"
    assert result.executed is False
    assert result.result is None

    event = gateway.audit_log.events[0]
    assert event.decision.outcome.value == "deny"
    assert event.decision.rule == "block-repository-deletion"


async def test_downstream_tool_level_errors_pass_through_transparently():
    # An invalid owner/repo is a normal GitHub API error (e.g. 404), which
    # the downstream GitHub MCP server reports as a valid MCP tool result
    # (CallToolResult.is_error=True) — not a gateway/authorization failure.
    gateway = make_gateway()
    async with gateway:
        result = await gateway.call_tool(
            "get_file_contents",
            {
                "owner": "agentshield-nonexistent-owner-xyz",
                "repo": "agentshield-nonexistent-repo-xyz",
                "path": "README",
            },
        )
    assert result.decision.outcome.value == "allow"
    assert result.executed is True
    assert result.result.is_error is True
