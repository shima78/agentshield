"""AgentShield MCP Gateway (Phase 2).

This subpackage depends on the Core (``agentshield.decision``, ``policy``,
``engine``, ``audit``); the Core has no dependency on MCP or on this
subpackage, and remains fully usable without it. MCP is the
transport/tool interface; AgentShield is the authorization boundary in
front of it.

Requires the optional ``mcp`` dependency: ``pip install -e ".[mcp]"``.
"""

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

__all__ = [
    "MCPGateway",
    "GatewayCallResult",
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
