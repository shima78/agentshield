"""In-memory audit trail for AgentShield.

Phase 1 provides only an in-memory collector. Persistent storage (a
database, log shipping, etc.) is out of scope for the Core.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict

from .decision import Decision
from .policy import DecisionRequest

ApprovalOutcome = Literal["approved", "rejected"]


class AuditEvent(BaseModel):
    """A single recorded decision.

    ``approval_required``/``approval_outcome`` are optional, backward-compatible
    fields: callers that never deal with REVIEW/approval (e.g. Core-only
    usage) can ignore them entirely and they default to "no approval was
    involved".
    """

    model_config = ConfigDict(frozen=True)

    timestamp: datetime
    request: DecisionRequest
    decision: Decision
    approval_required: bool = False
    approval_outcome: Optional[ApprovalOutcome] = None


class AuditLog:
    """An in-memory, append-only collection of ``AuditEvent`` records."""

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

    def record(
        self,
        request: DecisionRequest,
        decision: Decision,
        *,
        approval_required: bool = False,
        approval_outcome: Optional[ApprovalOutcome] = None,
    ) -> AuditEvent:
        """Record a decision and return the stored event."""
        event = AuditEvent(
            timestamp=datetime.now(timezone.utc),
            request=request,
            decision=decision,
            approval_required=approval_required,
            approval_outcome=approval_outcome,
        )
        self._events.append(event)
        return event

    @property
    def events(self) -> list[AuditEvent]:
        """A snapshot copy of the recorded events, oldest first."""
        return list(self._events)
