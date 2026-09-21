"""Gateway-level exceptions.

These are distinct from Core exceptions (e.g. ``agentshield.PolicyError``):
they describe failures of the MCP transport/proxy layer, not of policy
evaluation itself.
"""

from __future__ import annotations


class GatewayError(Exception):
    """Base class for all AgentShield MCP gateway errors."""


class GatewayConfigError(GatewayError):
    """Raised when gateway configuration is missing or malformed."""


class DownstreamConnectionError(GatewayError):
    """Raised when the gateway cannot connect to, or loses, the downstream MCP server."""


class DownstreamToolError(GatewayError):
    """Raised when the downstream MCP server fails to execute a forwarded tool call."""


class AuthorizationEvaluationError(GatewayError):
    """Raised when the ``DecisionEngine`` itself fails to produce a decision.

    The gateway fails closed: a tool call is never forwarded downstream when
    this happens.
    """


class ApprovalProviderRequiredError(GatewayError):
    """Raised when a REVIEW decision is reached but no ``ApprovalProvider`` is configured.

    The gateway fails closed: a tool call is never forwarded downstream when
    this happens.
    """


class ApprovalProviderError(GatewayError):
    """Raised when the configured ``ApprovalProvider`` itself fails."""
