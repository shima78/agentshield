"""The generic semantic evaluation interface (optional).

This module defines the interface ``DecisionEngine`` depends on for
optional semantic evaluation — it has no dependency on any specific
provider. ``agentshield.jev`` is one implementation of this interface
(backed by TypeSafe's Jev model); it is not imported here. This mirrors
the Core/MCP-adapter dependency direction: Core defines the interface,
an optional adapter implements it.

Deterministic policy remains authoritative. ``DecisionEngine`` never
consults a ``SemanticEvaluator`` for a request that deterministic policy
already denies, and a semantic assessment can only ever escalate an
ALLOW toward REVIEW — never produce DENY, and never downgrade an
existing REVIEW or DENY. See ``engine.py`` for exactly how the two are
combined.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .policy import DecisionRequest, PolicyRule


class SemanticVerdict(str, Enum):
    """A semantic assessment of a proposed decision's quality/appropriateness.

    This is deliberately not a security/authorization verdict — a
    technically-permitted action can still be judged a bad idea (e.g. a
    database migration proposed for Friday evening in production).
    """

    GOOD = "good"
    REVIEW = "review"
    BAD = "bad"


@dataclass(frozen=True)
class SemanticAssessment:
    """The result of asking a ``SemanticEvaluator`` about one decision."""

    verdict: SemanticVerdict
    confidence: float
    reason: Optional[str] = None


class SemanticEvaluator(abc.ABC):
    """Adds semantic judgment on top of a deterministic policy decision."""

    @abc.abstractmethod
    def assess(
        self, request: DecisionRequest, rule: Optional[PolicyRule]
    ) -> SemanticAssessment:
        """Assess whether ``request`` looks like a sensible decision.

        ``rule`` is the policy rule the deterministic engine matched for
        this request (``None`` if none matched, i.e. the default-allow
        case) — the minimal, relevant slice of policy for this one
        request, never the whole policy file.
        """
        raise NotImplementedError
