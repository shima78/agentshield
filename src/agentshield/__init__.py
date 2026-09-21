"""AgentShield Core — a policy-driven decision engine for AI agents and
AI-native applications.

    decision = shield.evaluate(request)

AgentShield decides; the agent/application remains responsible for
executing (or not executing) the action. This package exposes only the
Core: deterministic policy evaluation, typed decisions, and an in-memory
audit trail. It has no dependency on MCP or any other transport/tool
protocol — ``agentshield.mcp`` is an optional adapter built on top of it,
not the other way around. See the project README for architecture and
roadmap.

``DecisionEngine``/``DecisionRequest`` are the current names for what was
previously ``AuthorizationEngine``/``AuthorizationRequest``, reflecting
that this is a general-purpose decision engine, not an MCP-specific
authorization layer. The old names remain importable as aliases; the
request's tool/action field was renamed from ``tool`` to ``action``
(a breaking change for any code constructing the request with a ``tool=``
keyword — see the README).

``DecisionEngine`` can optionally be given a ``SemanticEvaluator`` to add
semantic judgment (is this action a *sensible* one, not just a permitted
one?) on top of deterministic policy — see ``agentshield.jev`` for a
provider backed by TypeSafe's Jev model. This is entirely optional: the
Core has no dependency on Jev (or any other semantic provider) either, and
``agentshield.jev`` is never imported here.
"""

from .audit import AuditEvent, AuditLog
from .decision import Decision, Outcome
from .engine import DecisionEngine
from .policy import DecisionRequest, Policy, PolicyError, PolicyRule
from .risk import RiskLevel
from .semantic import SemanticAssessment, SemanticEvaluator, SemanticVerdict

# Deprecated aliases, kept for a soft transition. Prefer DecisionEngine /
# DecisionRequest in new code.
AuthorizationEngine = DecisionEngine
AuthorizationRequest = DecisionRequest

__all__ = [
    "DecisionEngine",
    "DecisionRequest",
    "AuditEvent",
    "AuditLog",
    "Decision",
    "Outcome",
    "Policy",
    "PolicyError",
    "PolicyRule",
    "RiskLevel",
    "SemanticAssessment",
    "SemanticEvaluator",
    "SemanticVerdict",
    # Deprecated aliases
    "AuthorizationEngine",
    "AuthorizationRequest",
]
