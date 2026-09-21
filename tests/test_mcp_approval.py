import pytest

from agentshield import Decision, DecisionRequest, Outcome, RiskLevel
from agentshield.mcp import ApprovalResult, CallbackApprovalProvider


def make_request():
    return DecisionRequest(actor="agent", server="demo", action="create_file", arguments={})


def make_decision():
    return Decision.from_outcome(Outcome.REVIEW, RiskLevel.MEDIUM, "needs approval")


async def test_callback_provider_supports_sync_callback():
    def approve(request, decision) -> ApprovalResult:
        return ApprovalResult(approved=True, approver="tester")

    provider = CallbackApprovalProvider(approve)
    result = await provider.request_approval(make_request(), make_decision())
    assert result.approved is True
    assert result.approver == "tester"


async def test_callback_provider_supports_async_callback():
    async def approve(request, decision) -> ApprovalResult:
        return ApprovalResult(approved=False, reason="not today")

    provider = CallbackApprovalProvider(approve)
    result = await provider.request_approval(make_request(), make_decision())
    assert result.approved is False
    assert result.reason == "not today"


async def test_callback_provider_propagates_callback_exceptions():
    def approve(request, decision) -> ApprovalResult:
        raise RuntimeError("boom")

    provider = CallbackApprovalProvider(approve)
    with pytest.raises(RuntimeError):
        await provider.request_approval(make_request(), make_decision())
