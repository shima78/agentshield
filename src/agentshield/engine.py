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
Core selects a decision, no other layer — including an optional semantic
evaluator such as ``agentshield.jev`` — may override a DENY. See the
README for the full rationale.

Optional semantic evaluation: if a ``SemanticEvaluator`` is configured, it
is consulted for every non-DENY decision and can escalate ALLOW toward
REVIEW when it judges the action semantically questionable — it can never
produce DENY, and never touches an outcome that is already DENY or REVIEW.
Deterministic policy remains authoritative.
"""

from __future__ import annotations

from typing import Optional

from .decision import Decision, Outcome
from .policy import DecisionRequest, Policy, PolicyRule
from .risk import RiskLevel
from .semantic import SemanticEvaluator, SemanticVerdict

DEFAULT_ALLOW_REASON = "No policy matched; action allowed by default."


class DecisionEngine:
    """Evaluates decision requests against a policy."""

    def __init__(
        self, policy: Policy, *, semantic_evaluator: Optional[SemanticEvaluator] = None
    ) -> None:
        self._policy = policy
        self.semantic_evaluator = semantic_evaluator

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
            decision = Decision.from_outcome(
                outcome=Outcome.ALLOW,
                risk=RiskLevel.LOW,
                reason=DEFAULT_ALLOW_REASON,
                confidence=1.0,
            )
            matched_rule = None
        else:
            _, best_rule = max(
                matches,
                key=lambda pair: self._precedence_key(pair[1], request, pair[0]),
            )
            reason = best_rule.reason or f"Matched policy rule '{best_rule.name}'."
            decision = Decision.from_outcome(
                outcome=best_rule.outcome,
                risk=best_rule.risk,
                reason=reason,
                rule=best_rule.name,
                confidence=1.0,
            )
            matched_rule = best_rule

        if self.semantic_evaluator is None or decision.outcome == Outcome.DENY:
            return decision
        return self._apply_semantic_assessment(decision, request, matched_rule)

    def _apply_semantic_assessment(
        self, decision: Decision, request: DecisionRequest, rule: Optional[PolicyRule]
    ) -> Decision:
        assert self.semantic_evaluator is not None
        assessment = self.semantic_evaluator.assess(request, rule)

        outcome = decision.outcome
        if outcome == Outcome.ALLOW and assessment.verdict != SemanticVerdict.GOOD:
            # Additive only: semantic judgment can raise caution, never grant it.
            outcome = Outcome.REVIEW

        if assessment.reason:
            reason = f"{decision.reason} Semantic assessment ({assessment.verdict.value}): {assessment.reason}"
        else:
            reason = f"{decision.reason} Semantic assessment: {assessment.verdict.value}."

        return Decision.from_outcome(
            outcome=outcome,
            risk=decision.risk,
            reason=reason,
            rule=decision.rule,
            confidence=assessment.confidence,
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
