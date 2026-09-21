"""The approval abstraction used to resolve ``Outcome.REVIEW`` decisions.

Phase 2 only needs a minimal, swappable interface. ``CallbackApprovalProvider``
covers scripted/test/programmatic approval; ``ConsoleApprovalProvider`` is a
convenience for local demos. Web, Slack, CLI, or enterprise approval
workflows can be added later as additional ``ApprovalProvider``
implementations without touching the gateway.
"""

from __future__ import annotations

import abc
import inspect
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional, Union

from ..decision import Decision
from ..policy import AuthorizationRequest


@dataclass(frozen=True)
class ApprovalResult:
    """The outcome of asking an ``ApprovalProvider`` to resolve a REVIEW decision."""

    approved: bool
    approver: Optional[str] = None
    reason: Optional[str] = None


class ApprovalProvider(abc.ABC):
    """Resolves a REVIEW decision into an approve/reject outcome."""

    @abc.abstractmethod
    async def request_approval(
        self, request: AuthorizationRequest, decision: Decision
    ) -> ApprovalResult:
        """Ask whatever backs this provider whether ``request`` should proceed."""
        raise NotImplementedError


ApprovalCallback = Callable[
    [AuthorizationRequest, Decision], Union[ApprovalResult, Awaitable[ApprovalResult]]
]


class CallbackApprovalProvider(ApprovalProvider):
    """Wraps a plain sync or async callable as an ``ApprovalProvider``.

    This is the primary building block for tests and for programmatic
    integrations (e.g. a webhook handler, a queue consumer) that already
    have their own way of deciding approve/reject.
    """

    def __init__(self, callback: ApprovalCallback) -> None:
        self._callback = callback

    async def request_approval(
        self, request: AuthorizationRequest, decision: Decision
    ) -> ApprovalResult:
        result = self._callback(request, decision)
        if inspect.isawaitable(result):
            result = await result
        return result


class ConsoleApprovalProvider(ApprovalProvider):
    """Prompts a human at the terminal. Intended for local demos only."""

    async def request_approval(
        self, request: AuthorizationRequest, decision: Decision
    ) -> ApprovalResult:
        print(
            f"\n[AgentShield] REVIEW required for tool '{request.tool}' "
            f"on server '{request.server}'"
        )
        print(f"  risk={decision.risk.value} reason={decision.reason}")
        print(f"  arguments={request.arguments}")
        answer = input("Approve? [y/N]: ").strip().lower()
        approved = answer in ("y", "yes")
        return ApprovalResult(approved=approved, approver="console")
