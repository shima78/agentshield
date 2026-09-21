"""Tests for DecisionEngine's optional semantic-evaluation combination logic.

These use an in-repo fake SemanticEvaluator (implementing the Core's own
``SemanticEvaluator`` interface) rather than the real Jev-backed one, so
they have no dependency on ``typesafe_sdk`` and always run. Jev-specific
behavior (building the request, parsing a real ChoiceAnswer) is covered
separately in test_jev_evaluator.py.

Semantic evaluation is only ever consulted for an ALLOW decision:

    DENY   -> final, Jev never consulted
    REVIEW -> final, Jev never consulted (deterministic policy already
              asked for human attention; there is nothing more to add)
    ALLOW  -> Jev consulted, may escalate to REVIEW, never to DENY
"""

from dataclasses import dataclass
from typing import Optional

from agentshield import (
    DecisionEngine,
    DecisionRequest,
    Outcome,
    Policy,
    PolicyRule,
    RiskLevel,
    SemanticAssessment,
    SemanticEvaluator,
    SemanticVerdict,
)

POLICY = Policy.from_dict(
    {
        "rules": [
            {"name": "allow-echo", "tool": "echo", "outcome": "allow", "risk": "low"},
            {
                "name": "deny-delete",
                "tool": "delete_repository",
                "outcome": "deny",
                "risk": "critical",
            },
            {
                "name": "review-merge",
                "tool": "merge_pull_request",
                "outcome": "review",
                "risk": "high",
            },
        ]
    }
)


@dataclass
class FakeSemanticEvaluator(SemanticEvaluator):
    """Returns a fixed assessment and records every call it receives."""

    verdict: SemanticVerdict
    confidence: float = 0.8
    reason: Optional[str] = None
    calls: list = None

    def __post_init__(self):
        if self.calls is None:
            self.calls = []

    def assess(self, request: DecisionRequest, rule) -> SemanticAssessment:
        self.calls.append((request, rule))
        return SemanticAssessment(verdict=self.verdict, confidence=self.confidence, reason=self.reason)


def make_request(**overrides) -> DecisionRequest:
    defaults = dict(actor="agent", action="echo", arguments={}, context={})
    defaults.update(overrides)
    return DecisionRequest(**defaults)


# --- 1: Jev disabled/unavailable -> Core still works ------------------


def test_engine_without_semantic_evaluator_behaves_exactly_as_before():
    engine = DecisionEngine(POLICY)
    decision = engine.evaluate(make_request(action="echo"))
    assert decision.outcome == Outcome.ALLOW
    assert decision.confidence == 1.0
    assert decision.rule == "allow-echo"


# --- 2: deterministic DENY cannot be overridden ------------------------


def test_deny_is_never_sent_to_semantic_evaluator_or_overridden():
    evaluator = FakeSemanticEvaluator(verdict=SemanticVerdict.BAD)
    engine = DecisionEngine(POLICY, semantic_evaluator=evaluator)
    decision = engine.evaluate(make_request(action="delete_repository"))

    assert decision.outcome == Outcome.DENY
    assert decision.allowed is False
    assert decision.rule == "deny-delete"
    assert evaluator.calls == []  # never even consulted


# --- deterministic REVIEW is also final: Jev is not consulted -----------


def test_review_is_never_sent_to_semantic_evaluator():
    evaluator = FakeSemanticEvaluator(verdict=SemanticVerdict.BAD)
    engine = DecisionEngine(POLICY, semantic_evaluator=evaluator)
    decision = engine.evaluate(make_request(action="merge_pull_request"))

    assert decision.outcome == Outcome.REVIEW
    assert decision.rule == "review-merge"
    assert evaluator.calls == []  # never even consulted
    assert "Semantic assessment" not in decision.reason
    assert decision.confidence == 1.0  # untouched: no semantic evaluation ran


# --- 3: semantic evaluation is requested for an applicable action ------


def test_semantic_evaluator_is_consulted_for_allow_outcome():
    evaluator = FakeSemanticEvaluator(verdict=SemanticVerdict.GOOD)
    engine = DecisionEngine(POLICY, semantic_evaluator=evaluator)
    request = make_request(action="echo")
    engine.evaluate(request)

    assert len(evaluator.calls) == 1
    called_request, called_rule = evaluator.calls[0]
    assert called_request is request
    assert called_rule.name == "allow-echo"


# --- 4: structured semantic result is converted into a Decision --------


def test_good_verdict_keeps_allow():
    evaluator = FakeSemanticEvaluator(verdict=SemanticVerdict.GOOD, confidence=0.95)
    engine = DecisionEngine(POLICY, semantic_evaluator=evaluator)
    decision = engine.evaluate(make_request(action="echo"))

    assert decision.outcome == Outcome.ALLOW
    assert decision.allowed is True
    assert decision.confidence == 0.95
    assert "Semantic assessment" in decision.reason


def test_review_verdict_escalates_allow_to_review():
    evaluator = FakeSemanticEvaluator(verdict=SemanticVerdict.REVIEW, confidence=0.6)
    engine = DecisionEngine(POLICY, semantic_evaluator=evaluator)
    decision = engine.evaluate(make_request(action="echo"))

    assert decision.outcome == Outcome.REVIEW
    assert decision.allowed is False
    assert decision.confidence == 0.6
    assert decision.rule == "allow-echo"  # deterministic rule attribution preserved


def test_bad_verdict_escalates_allow_to_review_not_deny():
    evaluator = FakeSemanticEvaluator(verdict=SemanticVerdict.BAD, confidence=0.4)
    engine = DecisionEngine(POLICY, semantic_evaluator=evaluator)
    decision = engine.evaluate(make_request(action="echo"))

    # Semantic judgment can raise caution, never deny -- only deterministic
    # policy can produce DENY.
    assert decision.outcome == Outcome.REVIEW
    assert decision.allowed is False


def test_semantic_reason_is_appended_when_provided():
    evaluator = FakeSemanticEvaluator(
        verdict=SemanticVerdict.REVIEW, reason="Friday evening production deploy is risky."
    )
    engine = DecisionEngine(POLICY, semantic_evaluator=evaluator)
    decision = engine.evaluate(make_request(action="echo"))
    assert "Friday evening production deploy is risky." in decision.reason


# --- 5: applicable policy is included in the semantic evaluation input -


def test_matched_rule_is_passed_to_semantic_evaluator():
    evaluator = FakeSemanticEvaluator(verdict=SemanticVerdict.GOOD)
    engine = DecisionEngine(POLICY, semantic_evaluator=evaluator)
    engine.evaluate(make_request(action="echo"))

    _, rule = evaluator.calls[0]
    assert isinstance(rule, PolicyRule)
    assert rule.name == "allow-echo"
    assert rule.outcome == Outcome.ALLOW
    assert rule.risk == RiskLevel.LOW


def test_no_matching_rule_passes_none_to_semantic_evaluator():
    evaluator = FakeSemanticEvaluator(verdict=SemanticVerdict.GOOD)
    engine = DecisionEngine(POLICY, semantic_evaluator=evaluator)
    decision = engine.evaluate(make_request(action="completely_unrelated_action"))

    assert decision.outcome == Outcome.ALLOW
    _, rule = evaluator.calls[0]
    assert rule is None


# --- 6: existing non-Jev behavior is unchanged --------------------------


def test_default_allow_reason_unaffected_without_semantic_evaluator():
    engine = DecisionEngine(POLICY)
    decision = engine.evaluate(make_request(action="totally_unknown"))
    assert decision.reason == "No policy matched; action allowed by default."
    assert decision.confidence == 1.0
