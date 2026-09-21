"""The downstream MCP client connection.

This is the only module that speaks the MCP client protocol (via the
official ``mcp`` SDK). The gateway treats it as an opaque "list tools" /
"forward this tool call" interface and never bypasses it.
"""

from __future__ import annotations

from contextlib import AsyncExitStack
from typing import Any, Optional

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import CallToolResult, Tool

from .errors import DownstreamConnectionError, DownstreamToolError
from .models import DownstreamConfig


def _result_text(result: CallToolResult) -> str:
    parts = [
        text
        for block in (result.content or [])
        if (text := getattr(block, "text", None))
    ]
    return "; ".join(parts) or "downstream tool reported an error"


class DownstreamMCPProxy:
    """A client connection to a downstream MCP server, launched locally over stdio."""

    def __init__(self, config: DownstreamConfig) -> None:
        self._config = config
        self._exit_stack: Optional[AsyncExitStack] = None
        self._session: Optional[ClientSession] = None

    async def connect(self) -> None:
        if self._session is not None:
            return
        params = StdioServerParameters(
            command=self._config.command, args=self._config.args, env=self._config.env
        )
        stack = AsyncExitStack()
        try:
            read, write = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
        except Exception as exc:
            await stack.aclose()
            raise DownstreamConnectionError(
                f"Failed to connect to downstream MCP server "
                f"({self._config.command} {' '.join(self._config.args)}): {exc}"
            ) from exc
        self._exit_stack = stack
        self._session = session

    async def close(self) -> None:
        if self._exit_stack is not None:
            await self._exit_stack.aclose()
        self._exit_stack = None
        self._session = None

    async def __aenter__(self) -> "DownstreamMCPProxy":
        await self.connect()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    def _require_session(self) -> ClientSession:
        if self._session is None:
            raise DownstreamConnectionError(
                "Not connected to the downstream MCP server; call connect() first."
            )
        return self._session

    async def list_tools(self) -> list[Tool]:
        """Return the downstream server's tool definitions, unmodified."""
        session = self._require_session()
        try:
            result = await session.list_tools()
        except Exception as exc:
            raise DownstreamConnectionError(f"Failed to list downstream tools: {exc}") from exc
        return result.tools

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> CallToolResult:
        """Forward a tool call downstream, unmodified, and return its result."""
        session = self._require_session()
        try:
            result = await session.call_tool(name, arguments)
        except Exception as exc:
            raise DownstreamToolError(f"Downstream tool '{name}' failed: {exc}") from exc

        if not isinstance(result, CallToolResult):
            raise DownstreamToolError(
                f"Unexpected result type from downstream tool '{name}': "
                f"{type(result).__name__}"
            )
        if result.is_error:
            raise DownstreamToolError(
                f"Downstream tool '{name}' returned an error: {_result_text(result)}"
            )
        return result
