"""Risk level model for AgentShield.

Phase 1 treats risk as purely policy-defined: a rule author assigns a
``RiskLevel`` to a rule, and that value flows through unchanged into the
resulting ``Decision``. There is no automatic risk inference.
"""

from __future__ import annotations

from enum import Enum


class RiskLevel(str, Enum):
    """Coarse-grained risk classification for a policy decision."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"
