"""AgentShield MCP Gateway (Phase 2).

This subpackage depends on the Core (``agentshield.decision``, ``policy``,
``engine``, ``audit``); the Core has no dependency on MCP or on this
subpackage, and remains fully usable without it. MCP is the
transport/tool interface; AgentShield is the authorization boundary in
front of it.

Requires the optional ``mcp`` dependency: ``pip install -e ".[mcp]"``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .approval import (
    ApprovalProvider,
    ApprovalResult,
    CallbackApprovalProvider,
    ConsoleApprovalProvider,
)
from .errors import (
    ApprovalProviderError,
    ApprovalProviderRequiredError,
    AuthorizationEvaluationError,
    DownstreamConnectionError,
    DownstreamToolError,
    GatewayConfigError,
    GatewayError,
)
from .gateway import GatewayCallResult, MCPGateway
from .models import DownstreamConfig, GatewayConfig, PolicyConfig, ServerConfig
from .proxy import DownstreamMCPProxy

# AgentShieldMCPServer is exposed lazily (PEP 562, via __getattr__ below)
# rather than imported here: `python -m agentshield.mcp.server` imports
# this package first, then re-executes server.py as __main__. Eagerly
# importing .server above would register it in sys.modules under its
# normal name too, and Python warns about then running it as __main__.
if TYPE_CHECKING:  # pragma: no cover
    from .server import AgentShieldMCPServer

__all__ = [
    "MCPGateway",
    "GatewayCallResult",
    "AgentShieldMCPServer",
    "DownstreamMCPProxy",
    "DownstreamConfig",
    "GatewayConfig",
    "ServerConfig",
    "PolicyConfig",
    "ApprovalProvider",
    "ApprovalResult",
    "CallbackApprovalProvider",
    "ConsoleApprovalProvider",
    "GatewayError",
    "GatewayConfigError",
    "DownstreamConnectionError",
    "DownstreamToolError",
    "AuthorizationEvaluationError",
    "ApprovalProviderRequiredError",
    "ApprovalProviderError",
]


def __getattr__(name: str):
    if name == "AgentShieldMCPServer":
        from .server import AgentShieldMCPServer

        return AgentShieldMCPServer
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
