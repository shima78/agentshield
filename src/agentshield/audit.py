"""In-memory audit trail for AgentShield.

Phase 1 provides only an in-memory collector. Persistent storage (a
database, log shipping, etc.) is out of scope for the Core.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict

from .decision import Decision
from .policy import AuthorizationRequest


class AuditEvent(BaseModel):
    """A single recorded authorization decision."""

    model_config = ConfigDict(frozen=True)

    timestamp: datetime
    request: AuthorizationRequest
    decision: Decision


class AuditLog:
    """An in-memory, append-only collection of ``AuditEvent`` records."""

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

    def record(self, request: AuthorizationRequest, decision: Decision) -> AuditEvent:
        """Record an authorization decision and return the stored event."""
        event = AuditEvent(
            timestamp=datetime.now(timezone.utc),
            request=request,
            decision=decision,
        )
        self._events.append(event)
        return event

    @property
    def events(self) -> list[AuditEvent]:
        """A snapshot copy of the recorded events, oldest first."""
        return list(self._events)
