"""The upstream-facing AgentShield MCP server.

This is a thin adapter: it speaks MCP to an upstream client (initialize,
``tools/list``, ``tools/call``) using the official MCP SDK's low-level
``Server``, and delegates every authorization decision to the existing
``MCPGateway``. It contains no authorization logic of its own — this module
must never duplicate what ``AuthorizationEngine``/``MCPGateway`` already do.

    MCP Client
        |
        v
    AgentShieldMCPServer   (this module: MCP protocol only)
        |
        v
    MCPGateway              (agentshield.mcp.gateway: routes ALLOW/REVIEW/DENY)
        |
        v
    AuthorizationEngine      (agentshield.engine: the Core, MCP-agnostic)
        |
        v
    DownstreamMCPProxy      (agentshield.mcp.proxy: the real downstream MCP server)

Human-readable diagnostics go to stderr via the standard ``logging`` module.
stdout is reserved entirely for MCP protocol traffic — never write to it
directly (no ``print()``) anywhere in this module or its callees.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from typing import Any, Optional

from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.types import CallToolRequestParams, CallToolResult, ListToolsResult, TextContent, Tool

from .errors import (
    ApprovalProviderError,
    ApprovalProviderRequiredError,
    AuthorizationEvaluationError,
    DownstreamConnectionError,
    DownstreamToolError,
    GatewayError,
)
from .gateway import GatewayCallResult, MCPGateway
from .models import GatewayConfig

logger = logging.getLogger("agentshield.mcp.server")

# Client-facing messages for gateway-level failures. Deliberately generic:
# the real exception (which may include command lines, paths, or other
# internal detail) is logged to stderr, never sent to the MCP client.
_SAFE_ERROR_MESSAGES: tuple[tuple[type[GatewayError], str], ...] = (
    (
        AuthorizationEvaluationError,
        "AgentShield could not evaluate authorization for this action.",
    ),
    (
        ApprovalProviderRequiredError,
        "This action requires approval, but no approval provider is configured.",
    ),
    (ApprovalProviderError, "The approval provider failed while reviewing this action."),
    (DownstreamConnectionError, "AgentShield could not reach the downstream MCP server."),
    (DownstreamToolError, "The downstream MCP server failed to execute this tool."),
)


def _safe_message(exc: GatewayError) -> str:
    for exc_type, message in _SAFE_ERROR_MESSAGES:
        if isinstance(exc, exc_type):
            return message
    return "AgentShield encountered an internal gateway error."


def _text_result(text: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(type="text", text=text)], is_error=True)


def _blocked_message(result: GatewayCallResult) -> str:
    decision = result.decision
    if result.approval is not None and not result.approval.approved:
        headline = "This action was reviewed and not approved."
    else:
        headline = "This action was denied by policy."
    parts = [headline, f"risk: {decision.risk.value}"]
    if decision.rule:
        parts.append(f"rule: {decision.rule}")
    if decision.reason:
        parts.append(f"reason: {decision.reason}")
    return " | ".join(parts)


class AgentShieldMCPServer:
    """Exposes an ``MCPGateway`` as a real upstream MCP server over stdio.

    Every ``tools/call`` is routed through ``MCPGateway.call_tool()``
    unchanged; this class only translates the result into MCP protocol
    terms. A successful, executed call's downstream ``CallToolResult`` is
    returned to the client exactly as the downstream server produced it —
    including a downstream-reported error (``is_error=True``), which is a
    normal MCP result, not a gateway failure.
    """

    def __init__(self, gateway: MCPGateway, *, name: str = "agentshield") -> None:
        self.gateway = gateway
        self._server: Server[Any] = Server(
            name=name,
            on_list_tools=self._handle_list_tools,
            on_call_tool=self._handle_call_tool,
        )

    async def _handle_list_tools(self, context: Any, params: Any) -> ListToolsResult:
        tools: list[Tool] = await self.gateway.list_tools()
        return ListToolsResult(tools=tools)

    async def _handle_call_tool(
        self, context: Any, params: CallToolRequestParams
    ) -> CallToolResult:
        arguments = params.arguments or {}
        try:
            result = await self.gateway.call_tool(params.name, arguments)
        except GatewayError as exc:
            logger.error("gateway error handling tool call %r: %s", params.name, exc)
            return _text_result(_safe_message(exc))

        if not result.executed:
            logger.info(
                "blocked tool call %r: outcome=%s rule=%s",
                params.name,
                result.decision.outcome.value,
                result.decision.rule,
            )
            return _text_result(_blocked_message(result))

        assert result.result is not None
        return result.result

    async def run_stdio(self) -> None:
        """Connect downstream, then serve upstream MCP traffic over stdio until EOF."""
        logger.info("connecting to downstream MCP server")
        await self.gateway.connect()
        try:
            init_options = self._server.create_initialization_options()
            logger.info("agentshield MCP server ready, serving over stdio")
            async with stdio_server() as (read_stream, write_stream):
                await self._server.run(read_stream, write_stream, init_options)
        finally:
            logger.info("shutting down, closing downstream connection")
            await self.gateway.close()


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m agentshield.mcp.server",
        description="Run AgentShield as an upstream-facing MCP server over stdio.",
    )
    parser.add_argument(
        "--config", required=True, help="Path to a gateway YAML config file."
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Log level for stderr diagnostics (default: INFO).",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> None:
    args = _build_arg_parser().parse_args(argv)

    # MCP protocol traffic owns stdout; all diagnostics must go to stderr.
    logging.basicConfig(
        stream=sys.stderr,
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config = GatewayConfig.from_yaml(args.config)
    # No ApprovalProvider is wired up here: stdin/stdout are owned by the
    # MCP protocol stream in this process, so ConsoleApprovalProvider (which
    # reads stdin interactively) cannot be used. REVIEW decisions therefore
    # fail closed by default; embed AgentShieldMCPServer directly (rather
    # than this CLI) to wire up a CallbackApprovalProvider backed by Slack,
    # a web UI, etc.
    gateway = MCPGateway.from_config(config)
    server = AgentShieldMCPServer(gateway, name=f"agentshield-{config.server.name}")

    asyncio.run(server.run_stdio())


if __name__ == "__main__":
    main()
