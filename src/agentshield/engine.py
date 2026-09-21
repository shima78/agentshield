"""The decision engine.

``DecisionEngine`` evaluates a single ``DecisionRequest`` against a
``Policy`` and returns a typed ``Decision``. It never calls an external
service and never performs the action itself — AgentShield decides, the
agent/application executes. It has no dependency on MCP or any other
transport/tool protocol; an MCP adapter (``agentshield.mcp``) is one
possible caller among others.

Precedence (highest first) among rules that match a request:

1. Exact tool match beats wildcard tool match beats no tool constraint.
2. Within the same tool-match tier, more context constraints wins.
3. Within a further tie, more specific actor/server constraints wins
   (counting how many of {actor, server} the rule sets).
4. Within a full tie, the earlier rule in the policy's rule list wins.

This ordering depends only on the rule's own fields and its position in the
policy's rule list — never on dict iteration order — so it is fully
deterministic.

Safety note: precedence is resolved purely among policy rules. Once the
Core selects a decision, no other layer (including future providers such as
Jev) may override a DENY. See the README for the full rationale.
"""

from __future__ import annotations

from .decision import Decision, Outcome
from .policy import DecisionRequest, Policy, PolicyRule
from .risk import RiskLevel

DEFAULT_ALLOW_REASON = "No policy matched; action allowed by default."


class DecisionEngine:
    """Evaluates decision requests against a policy."""

    def __init__(self, policy: Policy) -> None:
        self._policy = policy

    @property
    def policy(self) -> Policy:
        return self._policy

    def evaluate(self, request: DecisionRequest) -> Decision:
        """Evaluate ``request`` and return the resulting ``Decision``."""
        matches = [
            (index, rule)
            for index, rule in enumerate(self._policy.rules)
            if rule.matches(request)
        ]

        if not matches:
            return Decision.from_outcome(
                outcome=Outcome.ALLOW,
                risk=RiskLevel.LOW,
                reason=DEFAULT_ALLOW_REASON,
                confidence=1.0,
            )

        _, best_rule = max(
            matches,
            key=lambda pair: self._precedence_key(pair[1], request, pair[0]),
        )

        reason = best_rule.reason or f"Matched policy rule '{best_rule.name}'."
        return Decision.from_outcome(
            outcome=best_rule.outcome,
            risk=best_rule.risk,
            reason=reason,
            rule=best_rule.name,
            confidence=1.0,
        )

    @staticmethod
    def _precedence_key(
        rule: PolicyRule, request: DecisionRequest, index: int
    ) -> tuple[int, int, int, int]:
        if rule.is_exact_tool_match(request):
            tool_specificity = 2
        elif rule.is_wildcard_tool_match(request):
            tool_specificity = 1
        else:
            tool_specificity = 0

        context_constraints = len(rule.context)
        actor_server_constraints = sum(
            1 for value in (rule.actor, rule.server) if value is not None
        )

        # Negative index: a strictly deterministic tiebreaker that always
        # prefers the earlier rule when every other criterion ties.
        return (tool_specificity, context_constraints, actor_server_constraints, -index)
