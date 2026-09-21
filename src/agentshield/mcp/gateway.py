"""The AgentShield MCP Gateway.

MCP is the transport/tool interface. AgentShield is the authorization
boundary. The gateway sits between an MCP client (an agent) and a
downstream MCP server: every tool call is evaluated by the Core's
``AuthorizationEngine`` before anything is forwarded downstream, and a
deterministic DENY can never be overridden here.

This module has no knowledge of any specific tool, provider, or MCP server
implementation — it only depends on the Core (``agentshield.decision``,
``policy``, ``engine``, ``audit``) and on ``DownstreamMCPProxy`` for
transport. The Core has no dependency on this module or on MCP at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from mcp.types import CallToolResult, Tool

from ..audit import AuditLog
from ..decision import Decision, Outcome
from ..engine import AuthorizationEngine
from ..policy import AuthorizationRequest, Policy
from .approval import ApprovalProvider, ApprovalResult
from .errors import (
    ApprovalProviderError,
    ApprovalProviderRequiredError,
    AuthorizationEvaluationError,
)
from .models import GatewayConfig
from .proxy import DownstreamMCPProxy


@dataclass(frozen=True)
class GatewayCallResult:
    """The outcome of routing a single tool call through the gateway.

    ``executed`` is True only when the call actually reached the downstream
    MCP server (an ALLOW, or a REVIEW that was approved). ``result`` is the
    downstream response in that case, and ``None`` otherwise. ``approval``
    is set only for REVIEW decisions.
    """

    decision: Decision
    executed: bool
    result: Optional[CallToolResult] = None
    approval: Optional[ApprovalResult] = None


class MCPGateway:
    """Enforces policy on every tool call between an MCP client and a downstream server."""

    def __init__(
        self,
        *,
        engine: AuthorizationEngine,
        downstream: DownstreamMCPProxy,
        server_name: str,
        actor: str = "agent",
        context: Optional[dict[str, Any]] = None,
        approval_provider: Optional[ApprovalProvider] = None,
        audit_log: Optional[AuditLog] = None,
    ) -> None:
        self.engine = engine
        self.downstream = downstream
        self.server_name = server_name
        self.actor = actor
        self.context = dict(context or {})
        self.approval_provider = approval_provider
        self.audit_log = audit_log if audit_log is not None else AuditLog()

    @classmethod
    def from_config(
        cls,
        config: GatewayConfig,
        *,
        approval_provider: Optional[ApprovalProvider] = None,
        audit_log: Optional[AuditLog] = None,
    ) -> "MCPGateway":
        """Build a gateway from a ``GatewayConfig`` (policy, downstream, context)."""
        policy = Policy.from_yaml(config.policy.path)
        engine = AuthorizationEngine(policy)
        downstream = DownstreamMCPProxy(config.downstream)
        return cls(
            engine=engine,
            downstream=downstream,
            server_name=config.server.name,
            actor=config.actor,
            context=config.context,
            approval_provider=approval_provider,
            audit_log=audit_log,
        )

    async def connect(self) -> None:
        await self.downstream.connect()

    async def close(self) -> None:
        await self.downstream.close()

    async def __aenter__(self) -> "MCPGateway":
        await self.connect()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.close()

    async def list_tools(self) -> list[Tool]:
        """Return the downstream server's tool definitions, unmodified.

        AgentShield is a policy layer, not a schema transformation layer:
        tool names, descriptions, and input schemas pass through as-is.
        """
        return await self.downstream.list_tools()

    def _build_request(self, tool: str, arguments: dict[str, Any]) -> AuthorizationRequest:
        return AuthorizationRequest(
            actor=self.actor,
            server=self.server_name,
            tool=tool,
            arguments=dict(arguments),
            context=dict(self.context),
        )

    async def call_tool(self, tool: str, arguments: dict[str, Any]) -> GatewayCallResult:
        """Authorize, then (if permitted) forward, a single tool call.

        Every call is evaluated by the ``AuthorizationEngine`` first. ALLOW
        forwards the call unmodified; DENY never reaches the downstream
        server; REVIEW is resolved through the configured
        ``ApprovalProvider`` before anything is forwarded.
        """
        request = self._build_request(tool, arguments)

        try:
            decision = self.engine.evaluate(request)
        except Exception as exc:
            # Fail closed: if authorization itself cannot be evaluated, the
            # call is never forwarded downstream.
            raise AuthorizationEvaluationError(
                f"Failed to evaluate authorization for tool '{tool}': {exc}"
            ) from exc

        if decision.outcome == Outcome.DENY:
            self.audit_log.record(request, decision)
            return GatewayCallResult(decision=decision, executed=False)

        if decision.outcome == Outcome.REVIEW:
            approval = await self._get_approval(request, decision)
            self.audit_log.record(
                request,
                decision,
                approval_required=True,
                approval_outcome="approved" if approval.approved else "rejected",
            )
            if not approval.approved:
                return GatewayCallResult(decision=decision, executed=False, approval=approval)
            result = await self.downstream.call_tool(tool, arguments)
            return GatewayCallResult(
                decision=decision, executed=True, result=result, approval=approval
            )

        # ALLOW
        self.audit_log.record(request, decision)
        result = await self.downstream.call_tool(tool, arguments)
        return GatewayCallResult(decision=decision, executed=True, result=result)

    async def _get_approval(
        self, request: AuthorizationRequest, decision: Decision
    ) -> ApprovalResult:
        if self.approval_provider is None:
            raise ApprovalProviderRequiredError(
                f"Tool '{request.tool}' requires REVIEW approval but no "
                f"ApprovalProvider is configured."
            )
        try:
            return await self.approval_provider.request_approval(request, decision)
        except Exception as exc:
            raise ApprovalProviderError(f"Approval provider failed: {exc}") from exc
