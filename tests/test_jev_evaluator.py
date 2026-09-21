"""Tests for JevSemanticEvaluator (agentshield.jev), with the real
typesafe_sdk client's network call mocked out.

These need the optional ``typesafe_sdk`` package importable (skipped
cleanly otherwise via ``pytest.importorskip``), but never touch the
network or require real credentials — deterministic, offline unit tests.
Real network calls against the live Jev API are covered separately and
explicitly in test_jev_integration.py.
"""

from unittest.mock import MagicMock

import pytest

ts = pytest.importorskip("typesafe_sdk", reason="requires the optional 'jev' extra")

from agentshield import DecisionRequest, Outcome, PolicyRule, RiskLevel, SemanticVerdict
from agentshield.jev import JevSemanticEvaluator


def _make_response(choice: str, confidence: float) -> ts.SystemOneResponse:
    return ts.SystemOneResponse(
        model="jev-latest",
        usage=ts.Usage(input_tokens=10, output_tokens=2),
        answers={
            "assessment": ts.ChoiceAnswer(
                choice=choice, confidence=confidence, probabilities={choice: confidence}
            )
        },
    )


def test_assess_calls_system_one_and_maps_good_verdict():
    client = MagicMock()
    client.system_one.return_value = _make_response("good", 0.93)
    evaluator = JevSemanticEvaluator(client=client)

    request = DecisionRequest(actor="agent", action="deploy", arguments={}, context={})
    assessment = evaluator.assess(request, None)

    assert assessment.verdict == SemanticVerdict.GOOD
    assert assessment.confidence == 0.93
    client.system_one.assert_called_once()


def test_assess_maps_review_and_bad_verdicts():
    client = MagicMock()

    client.system_one.return_value = _make_response("review", 0.5)
    evaluator = JevSemanticEvaluator(client=client)
    request = DecisionRequest(actor="agent", action="deploy")
    assert evaluator.assess(request, None).verdict == SemanticVerdict.REVIEW

    client.system_one.return_value = _make_response("bad", 0.2)
    assert evaluator.assess(request, None).verdict == SemanticVerdict.BAD


def test_assess_sends_action_actor_arguments_and_context_in_state():
    client = MagicMock()
    client.system_one.return_value = _make_response("good", 0.9)
    evaluator = JevSemanticEvaluator(client=client)

    request = DecisionRequest(
        actor="release-agent",
        action="deploy",
        server="prod-cluster",
        arguments={"service": "billing"},
        context={"environment": "production", "time": "friday_evening"},
    )
    evaluator.assess(request, None)

    _, kwargs = client.system_one.call_args
    state = kwargs["state"]
    assert state["action"] == "deploy"
    assert state["actor"] == "release-agent"
    assert state["target"] == "prod-cluster"
    assert state["arguments"] == {"service": "billing"}
    assert state["context"] == {"environment": "production", "time": "friday_evening"}


def test_assess_sends_choice_question_with_good_review_bad_criteria():
    client = MagicMock()
    client.system_one.return_value = _make_response("good", 0.9)
    evaluator = JevSemanticEvaluator(client=client)
    request = DecisionRequest(actor="agent", action="deploy")
    evaluator.assess(request, None)

    _, kwargs = client.system_one.call_args
    question = kwargs["questions"]["assessment"]
    assert set(question.criteria.keys()) == {"good", "review", "bad"}


# --- 5 (Jev-specific slice): applicable policy in the state, not the whole file --


def test_assess_includes_only_the_matched_rule_summary_not_the_full_policy():
    client = MagicMock()
    client.system_one.return_value = _make_response("good", 0.9)
    evaluator = JevSemanticEvaluator(client=client)

    rule = PolicyRule(
        name="production-merge",
        tool="merge_pull_request",
        context={"environment": "production"},
        outcome="review",
        risk="high",
        reason="Production merges require human approval.",
    )
    request = DecisionRequest(actor="agent", action="merge_pull_request")
    evaluator.assess(request, rule)

    _, kwargs = client.system_one.call_args
    applicable_policy = kwargs["state"]["applicable_policy"]
    assert applicable_policy == {
        "matched_rule": "production-merge",
        "outcome": "review",
        "risk": "high",
        "reason": "Production merges require human approval.",
    }
    # Only this one rule's summary is present -- no "rules" list, no
    # unrelated policy content leaked into the semantic evaluation input.
    assert "rules" not in kwargs["state"]


def test_assess_with_no_matched_rule_notes_default_allow():
    client = MagicMock()
    client.system_one.return_value = _make_response("good", 0.9)
    evaluator = JevSemanticEvaluator(client=client)
    request = DecisionRequest(actor="agent", action="unmatched_action")
    evaluator.assess(request, None)

    _, kwargs = client.system_one.call_args
    assert kwargs["state"]["applicable_policy"]["matched_rule"] is None


def test_evaluator_defaults_to_a_real_typesafe_client_when_none_given(monkeypatch):
    monkeypatch.setenv("TYPESAFE_API_KEY", "fake-key-for-construction-only")
    evaluator = JevSemanticEvaluator()
    assert isinstance(evaluator._client, ts.TypeSafeClient)


def test_evaluator_construction_fails_clearly_without_an_api_key(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    with pytest.raises(ts.TypeSafeError):
        JevSemanticEvaluator()
