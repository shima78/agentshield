"""Decision model for AgentShield.

A ``Decision`` is the sole output of the authorization engine: a typed,
immutable record of whether an action is allowed, why, and how risky it is
judged to be.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .risk import RiskLevel


class Outcome(str, Enum):
    """The possible results of a policy evaluation."""

    ALLOW = "allow"
    REVIEW = "review"
    DENY = "deny"


# The mapping from outcome to `allowed` is fixed and part of the Core's
# safety contract: only ALLOW is permitted to execute unattended.
_ALLOWED_BY_OUTCOME: dict[Outcome, bool] = {
    Outcome.ALLOW: True,
    Outcome.REVIEW: False,
    Outcome.DENY: False,
}


class Decision(BaseModel):
    """The result of evaluating an ``AuthorizationRequest`` against a policy."""

    model_config = ConfigDict(frozen=True)

    outcome: Outcome
    allowed: bool
    risk: RiskLevel
    confidence: Optional[float] = Field(default=1.0, ge=0.0, le=1.0)
    reason: str
    rule: Optional[str] = None

    @model_validator(mode="after")
    def _check_allowed_matches_outcome(self) -> "Decision":
        expected = _ALLOWED_BY_OUTCOME[self.outcome]
        if self.allowed != expected:
            raise ValueError(
                f"allowed={self.allowed!r} is inconsistent with outcome="
                f"{self.outcome.value!r}; expected allowed={expected!r}"
            )
        return self

    @classmethod
    def from_outcome(
        cls,
        outcome: Outcome,
        risk: RiskLevel,
        reason: str,
        rule: Optional[str] = None,
        confidence: Optional[float] = 1.0,
    ) -> "Decision":
        """Build a ``Decision`` with ``allowed`` derived from ``outcome``."""
        return cls(
            outcome=outcome,
            allowed=_ALLOWED_BY_OUTCOME[outcome],
            risk=risk,
            confidence=confidence,
            reason=reason,
            rule=rule,
        )
