import pytest
from pydantic import ValidationError

from agentshield import Decision, Outcome, RiskLevel


def test_allow_produces_allowed_true():
    decision = Decision.from_outcome(Outcome.ALLOW, RiskLevel.LOW, "ok")
    assert decision.allowed is True


def test_review_produces_allowed_false():
    decision = Decision.from_outcome(Outcome.REVIEW, RiskLevel.MEDIUM, "needs review")
    assert decision.allowed is False


def test_deny_produces_allowed_false():
    decision = Decision.from_outcome(Outcome.DENY, RiskLevel.HIGH, "blocked")
    assert decision.allowed is False


def test_inconsistent_allowed_raises():
    with pytest.raises(ValidationError):
        Decision(outcome=Outcome.DENY, allowed=True, risk=RiskLevel.HIGH, reason="x")


def test_confidence_defaults_to_one():
    decision = Decision.from_outcome(Outcome.ALLOW, RiskLevel.LOW, "ok")
    assert decision.confidence == 1.0


def test_confidence_rejects_out_of_range_values():
    with pytest.raises(ValidationError):
        Decision(outcome=Outcome.ALLOW, allowed=True, risk=RiskLevel.LOW, reason="x", confidence=1.5)
    with pytest.raises(ValidationError):
        Decision(outcome=Outcome.ALLOW, allowed=True, risk=RiskLevel.LOW, reason="x", confidence=-0.1)


def test_confidence_accepts_boundary_values():
    Decision(outcome=Outcome.ALLOW, allowed=True, risk=RiskLevel.LOW, reason="x", confidence=0.0)
    Decision(outcome=Outcome.ALLOW, allowed=True, risk=RiskLevel.LOW, reason="x", confidence=1.0)


def test_risk_enum_validation():
    with pytest.raises(ValidationError):
        Decision(outcome=Outcome.ALLOW, allowed=True, risk="extreme", reason="x")


def test_outcome_enum_validation():
    with pytest.raises(ValidationError):
        Decision(outcome="maybe", allowed=True, risk=RiskLevel.LOW, reason="x")


def test_decision_is_frozen():
    decision = Decision.from_outcome(Outcome.ALLOW, RiskLevel.LOW, "ok")
    with pytest.raises(ValidationError):
        decision.reason = "changed"


def test_rule_field_defaults_to_none():
    decision = Decision.from_outcome(Outcome.ALLOW, RiskLevel.LOW, "ok")
    assert decision.rule is None


def test_rule_field_can_be_set():
    decision = Decision.from_outcome(Outcome.DENY, RiskLevel.CRITICAL, "blocked", rule="my-rule")
    assert decision.rule == "my-rule"
