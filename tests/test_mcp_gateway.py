"""Gateway-logic tests using a fake in-process downstream proxy.

These exercise authorization/approval/audit wiring deterministically and
fast, without spawning a real subprocess. End-to-end tests against a real
downstream MCP server live in test_mcp_proxy.py.
"""

from dataclasses import dataclass, field
from typing import Any

import pytest

from agentshield import AuditLog, DecisionEngine, Policy
from agentshield.mcp import (
    ApprovalProviderError,
    ApprovalProviderRequiredError,
    ApprovalResult,
    AuthorizationEvaluationError,
    CallbackApprovalProvider,
    GatewayConfig,
    MCPGateway,
)


@dataclass
class FakeCallResult:
    tool: str
    arguments: dict[str, Any]


class FakeDownstreamProxy:
    """A duck-typed stand-in for DownstreamMCPProxy that records calls."""

    def __init__(self) -> None:
        self.connected = False
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.tools = ["echo", "create_file", "delete_file"]

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.connected = False

    async def list_tools(self):
        return list(self.tools)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> FakeCallResult:
        self.calls.append((name, dict(arguments)))
        return FakeCallResult(tool=name, arguments=dict(arguments))


@dataclass
class FailingEngine:
    """A stand-in DecisionEngine whose evaluate() always raises."""

    def evaluate(self, request):
        raise RuntimeError("engine exploded")


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
            {
                "name": "production-only-write",
                "tool": "write_config",
                "context": {"environment": "production"},
                "outcome": "review",
                "risk": "high",
            },
        ]
    }
)


def make_gateway(**kwargs) -> tuple[MCPGateway, FakeDownstreamProxy]:
    proxy = FakeDownstreamProxy()
    engine = kwargs.pop("engine", None) or DecisionEngine(DEMO_POLICY)
    gateway = MCPGateway(
        engine=engine,
        downstream=proxy,
        server_name="demo",
        **kwargs,
    )
    return gateway, proxy


# --- ALLOW ----------------------------------------------------------------


async def test_allow_forwards_call_and_preserves_arguments():
    gateway, proxy = make_gateway()
    result = await gateway.call_tool("echo", {"message": "hi"})
    assert result.decision.outcome.value == "allow"
    assert result.executed is True
    assert proxy.calls == [("echo", {"message": "hi"})]
    assert result.result.arguments == {"message": "hi"}


async def test_allow_records_audit_event():
    gateway, _ = make_gateway()
    await gateway.call_tool("echo", {"message": "hi"})
    events = gateway.audit_log.events
    assert len(events) == 1
    assert events[0].decision.outcome.value == "allow"
    assert events[0].approval_required is False
    assert events[0].approval_outcome is None


# --- DENY -------------------------------------------------------------------


async def test_deny_blocks_call_before_downstream():
    gateway, proxy = make_gateway()
    result = await gateway.call_tool("delete_file", {"name": "important.txt"})
    assert result.decision.outcome.value == "deny"
    assert result.executed is False
    assert result.result is None
    assert proxy.calls == []


async def test_deny_preserves_rule_and_risk_and_reason():
    gateway, _ = make_gateway()
    result = await gateway.call_tool("delete_file", {"name": "x"})
    assert result.decision.rule == "deny-delete-file"
    assert result.decision.risk.value == "critical"
    assert result.decision.reason


async def test_deny_records_audit_event_without_approval():
    gateway, _ = make_gateway()
    await gateway.call_tool("delete_file", {"name": "x"})
    event = gateway.audit_log.events[0]
    assert event.decision.outcome.value == "deny"
    assert event.approval_required is False
    assert event.approval_outcome is None


# --- REVIEW -----------------------------------------------------------------


async def test_review_without_approval_provider_raises_and_never_forwards():
    gateway, proxy = make_gateway()
    with pytest.raises(ApprovalProviderRequiredError):
        await gateway.call_tool("create_file", {"name": "note.txt"})
    assert proxy.calls == []


async def test_review_approved_executes_downstream():
    approver = CallbackApprovalProvider(
        lambda request, decision: ApprovalResult(approved=True, approver="tester")
    )
    gateway, proxy = make_gateway(approval_provider=approver)
    result = await gateway.call_tool("create_file", {"name": "note.txt"})
    assert result.decision.outcome.value == "review"
    assert result.executed is True
    assert result.approval.approved is True
    assert proxy.calls == [("create_file", {"name": "note.txt"})]


async def test_review_rejected_does_not_execute():
    approver = CallbackApprovalProvider(
        lambda request, decision: ApprovalResult(approved=False, reason="no")
    )
    gateway, proxy = make_gateway(approval_provider=approver)
    result = await gateway.call_tool("create_file", {"name": "note.txt"})
    assert result.executed is False
    assert result.result is None
    assert result.approval.approved is False
    assert proxy.calls == []


async def test_review_records_approval_outcome_in_audit():
    approver = CallbackApprovalProvider(
        lambda request, decision: ApprovalResult(approved=True)
    )
    gateway, _ = make_gateway(approval_provider=approver)
    await gateway.call_tool("create_file", {"name": "note.txt"})
    event = gateway.audit_log.events[0]
    assert event.approval_required is True
    assert event.approval_outcome == "approved"


async def test_review_approval_provider_failure_raises_and_never_forwards():
    def broken(request, decision):
        raise RuntimeError("provider down")

    gateway, proxy = make_gateway(approval_provider=CallbackApprovalProvider(broken))
    with pytest.raises(ApprovalProviderError):
        await gateway.call_tool("create_file", {"name": "note.txt"})
    assert proxy.calls == []


# --- Context ------------------------------------------------------------


async def test_context_is_passed_into_authorization_request():
    gateway, proxy = make_gateway(
        context={"environment": "production"},
        approval_provider=CallbackApprovalProvider(
            lambda request, decision: ApprovalResult(approved=True)
        ),
    )
    result = await gateway.call_tool("write_config", {"key": "x"})
    assert result.decision.outcome.value == "review"
    assert result.decision.rule == "production-only-write"


async def test_missing_context_falls_back_to_default_allow():
    gateway, proxy = make_gateway(context={})
    result = await gateway.call_tool("write_config", {"key": "x"})
    assert result.decision.outcome.value == "allow"
    assert result.decision.rule is None


# --- Fail-closed on authorization failure ----------------------------------


async def test_authorization_evaluation_failure_is_fail_closed():
    gateway, proxy = make_gateway(engine=FailingEngine())
    with pytest.raises(AuthorizationEvaluationError):
        await gateway.call_tool("echo", {"message": "hi"})
    assert proxy.calls == []


# --- Audit covers every outcome ---------------------------------------------


async def test_audit_event_generated_for_every_call():
    approver = CallbackApprovalProvider(lambda request, decision: ApprovalResult(approved=True))
    gateway, _ = make_gateway(approval_provider=approver)
    await gateway.call_tool("echo", {"message": "hi"})
    await gateway.call_tool("delete_file", {"name": "x"})
    await gateway.call_tool("create_file", {"name": "note.txt"})
    assert len(gateway.audit_log.events) == 3


async def test_gateway_uses_provided_audit_log_instance():
    audit_log = AuditLog()
    gateway, _ = make_gateway(audit_log=audit_log)
    await gateway.call_tool("echo", {"message": "hi"})
    assert gateway.audit_log is audit_log
    assert len(audit_log.events) == 1


# --- list_tools passthrough ---------------------------------------------


async def test_list_tools_returns_downstream_tools_unmodified():
    gateway, proxy = make_gateway()
    tools = await gateway.list_tools()
    assert tools == proxy.tools


# --- connect/close lifecycle ----------------------------------------------


async def test_context_manager_connects_and_closes_downstream():
    gateway, proxy = make_gateway()
    async with gateway:
        assert proxy.connected is True
    assert proxy.connected is False


# --- from_config -------------------------------------------------------


def test_from_config_builds_a_working_gateway(tmp_path):
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        "rules:\n"
        "  - name: allow-echo\n"
        "    tool: echo\n"
        "    outcome: allow\n"
        "    risk: low\n",
        encoding="utf-8",
    )
    config = GatewayConfig.from_dict(
        {
            "server": {"name": "demo"},
            "downstream": {"command": "python", "args": ["unused.py"]},
            "policy": {"path": str(policy_path)},
            "context": {"environment": "production"},
        }
    )
    gateway = MCPGateway.from_config(config)
    assert gateway.server_name == "demo"
    assert gateway.context == {"environment": "production"}
    assert gateway.actor == "agent"
