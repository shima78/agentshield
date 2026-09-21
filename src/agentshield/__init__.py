"""AgentShield Core — a provider-agnostic policy and authorization layer
for AI agents and AI-native applications.

This package exposes only the Core: deterministic policy evaluation,
typed decisions, and an in-memory audit trail. See the project README for
architecture and roadmap.
"""

from .audit import AuditEvent, AuditLog
from .decision import Decision, Outcome
from .engine import AuthorizationEngine
from .policy import AuthorizationRequest, Policy, PolicyError, PolicyRule
from .risk import RiskLevel

__all__ = [
    "AuthorizationEngine",
    "AuthorizationRequest",
    "AuditEvent",
    "AuditLog",
    "Decision",
    "Outcome",
    "Policy",
    "PolicyError",
    "PolicyRule",
    "RiskLevel",
]
