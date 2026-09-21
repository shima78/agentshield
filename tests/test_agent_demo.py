"""Tests for examples/agent_demo.py's three scenarios.

Loaded directly from its file path (examples/ is not a package) so these
tests exercise the exact demo code, not a reimplementation of it.

Deterministic-logic tests use a fake, dependency-free SemanticEvaluator
(no typesafe_sdk / network / credentials needed) and always run. A small
set of real-Jev tests at the bottom are isolated and skip cleanly without
TYPESAFE_API_KEY -- see test_jev_integration.py for the general pattern.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib

import pytest

from agentshield import (
    Decision,
    DecisionEngine,
    DecisionRequest,
    Outcome,
    SemanticAssessment,
    SemanticEvaluator,
    SemanticVerdict,
)

MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "examples" / "agent_demo.py"


def _load_agent_demo():
    spec = importlib.util.spec_from_file_location("agent_demo", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


agent_demo = _load_agent_demo()


class _FakeSemanticEvaluator(SemanticEvaluator):
    """Returns a fixed verdict; records whether it was ever consulted."""

    def __init__(self, verdict: SemanticVerdict, confidence: float = 0.8):
        self.verdict = verdict
        self.confidence = confidence
        self.calls = 0

    def assess(self, request: DecisionRequest, rule) -> SemanticAssessment:
        self.calls += 1
        return SemanticAssessment(verdict=self.verdict, confidence=self.confidence)


SCENARIO_1 = dict(
    action="delete_database",
    context={"environment": "production"},
    arguments={},
)
SCENARIO_2 = dict(
    action="deploy",
    context={
        "environment": "staging",
        "database_migration": True,
        "time": "friday_evening",
        "change_size": "large",
    },
    arguments={"version": "2.4.1"},
)
SCENARIO_3 = dict(
    action="deploy",
    context={
        "environment": "staging",
        "database_migration": False,
        "time": "tuesday_morning",
        "change_size": "small",
    },
    arguments={"version": "2.4.1"},
)


def _jev_shield_with(verdict: SemanticVerdict) -> tuple[DecisionEngine, _FakeSemanticEvaluator]:
    evaluator = _FakeSemanticEvaluator(verdict)
    return DecisionEngine(agent_demo.POLICY, semantic_evaluator=evaluator), evaluator


# --- imports correctly -----------------------------------------------------


def test_demo_module_imports_and_exposes_expected_names():
    assert hasattr(agent_demo, "evaluate_scenario")
    assert hasattr(agent_demo, "run_scenario")
    assert hasattr(agent_demo, "_build_jev_shield")
    assert hasattr(agent_demo, "POLICY")


# --- 1: deterministic DENY remains DENY -------------------------------


def test_scenario_1_deny_is_not_overridden_and_jev_is_never_consulted():
    jev_shield, evaluator = _jev_shield_with(SemanticVerdict.BAD)
    deterministic, final = agent_demo.evaluate_scenario(**SCENARIO_1, jev_shield=jev_shield)

    assert deterministic.outcome == Outcome.DENY
    assert final.outcome == Outcome.DENY
    assert final.allowed is False
    assert evaluator.calls == 0  # DENY is final before Jev would ever be asked


def test_scenario_1_without_jev_configured_is_also_deny():
    deterministic, final = agent_demo.evaluate_scenario(**SCENARIO_1, jev_shield=None)
    assert deterministic.outcome == Outcome.DENY
    assert final.outcome == Outcome.DENY


# --- 2: deterministic ALLOW can become REVIEW via semantic evaluation ---


def test_scenario_2_allow_becomes_review_when_jev_flags_it():
    jev_shield, evaluator = _jev_shield_with(SemanticVerdict.REVIEW)
    deterministic, final = agent_demo.evaluate_scenario(**SCENARIO_2, jev_shield=jev_shield)

    assert deterministic.outcome == Outcome.ALLOW  # policy alone would allow it
    assert final.outcome == Outcome.REVIEW  # Jev's judgment escalates it
    assert final.allowed is False
    assert evaluator.calls == 1


def test_scenario_2_without_jev_stays_allow():
    deterministic, final = agent_demo.evaluate_scenario(**SCENARIO_2, jev_shield=None)
    assert deterministic.outcome == Outcome.ALLOW
    assert final.outcome == Outcome.ALLOW


# --- 3: deterministic ALLOW can remain ALLOW when Jev agrees ------------


def test_scenario_3_allow_stays_allow_when_jev_agrees():
    jev_shield, evaluator = _jev_shield_with(SemanticVerdict.GOOD)
    deterministic, final = agent_demo.evaluate_scenario(**SCENARIO_3, jev_shield=jev_shield)

    assert deterministic.outcome == Outcome.ALLOW
    assert final.outcome == Outcome.ALLOW
    assert final.allowed is True
    assert evaluator.calls == 1


# --- run_scenario drives simulated execution correctly -------------------


def test_run_scenario_executes_only_on_final_allow(capsys):
    jev_shield, _ = _jev_shield_with(SemanticVerdict.GOOD)
    decision, executed = agent_demo.run_scenario(
        3, "routine", "deploy v2.4.1", jev_shield=jev_shield, **SCENARIO_3
    )
    assert decision.outcome == Outcome.ALLOW
    assert executed is True
    assert "executed." in capsys.readouterr().out


def test_run_scenario_does_not_execute_on_review(capsys):
    jev_shield, _ = _jev_shield_with(SemanticVerdict.REVIEW)
    decision, executed = agent_demo.run_scenario(
        2, "risky", "deploy v2.4.1", jev_shield=jev_shield, **SCENARIO_2
    )
    assert decision.outcome == Outcome.REVIEW
    assert executed is False
    out = capsys.readouterr().out
    assert "executed." not in out
    assert "not executed" in out


def test_run_scenario_does_not_execute_on_deny(capsys):
    decision, executed = agent_demo.run_scenario(
        1, "prod delete", "delete_database", jev_shield=None, **SCENARIO_1
    )
    assert decision.outcome == Outcome.DENY
    assert executed is False
    out = capsys.readouterr().out
    assert "executed." not in out
    assert "not executed" in out


# --- 4 & 5: works without Jev, and never fabricates a semantic result ---


def test_build_jev_shield_is_none_without_api_key(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert agent_demo._build_jev_shield() is None


def test_demo_shows_not_evaluated_rather_than_a_fake_result_without_jev(capsys):
    agent_demo.run_scenario(2, "risky", "deploy v2.4.1", jev_shield=None, **SCENARIO_2)
    out = capsys.readouterr().out
    assert "not evaluated" in out
    # None of the real verdict words appear as if Jev had actually run.
    assert "GOOD" not in out
    assert "REVIEW\n" not in out  # "Final decision:\n  ALLOW" only, no fake "With Jev: REVIEW"


def test_semantic_verdict_label_returns_none_when_no_assessment_present():
    decision = Decision.from_outcome(Outcome.DENY, agent_demo.POLICY.rules[0].risk, "denied, no semantics")
    assert agent_demo._semantic_verdict_label(decision) is None


# --- Real Jev integration (isolated, skipped cleanly without credentials) -

_missing_reason = None
if not os.environ.get("TYPESAFE_API_KEY"):
    _missing_reason = "TYPESAFE_API_KEY is not set."
else:
    try:
        import typesafe_sdk  # noqa: F401
    except ImportError:
        _missing_reason = "typesafe_sdk is not installed."

requires_real_jev = pytest.mark.skipif(_missing_reason is not None, reason=_missing_reason or "")


@pytest.mark.integration
@requires_real_jev
def test_real_jev_scenario_1_deny_is_never_overridden():
    jev_shield = agent_demo._build_jev_shield()
    assert jev_shield is not None
    _, final = agent_demo.evaluate_scenario(**SCENARIO_1, jev_shield=jev_shield)
    assert final.outcome == Outcome.DENY


@pytest.mark.integration
@requires_real_jev
def test_real_jev_scenarios_2_and_3_produce_well_formed_decisions():
    jev_shield = agent_demo._build_jev_shield()
    assert jev_shield is not None

    for scenario in (SCENARIO_2, SCENARIO_3):
        deterministic, final = agent_demo.evaluate_scenario(**scenario, jev_shield=jev_shield)
        assert deterministic.outcome == Outcome.ALLOW
        # We do not assert a specific Jev verdict -- that is Jev's real
        # judgment, not ours to hard-code -- only that the engine produced
        # a valid, well-formed decision from it.
        assert final.outcome in (Outcome.ALLOW, Outcome.REVIEW)
        assert final.confidence is not None
        assert 0.0 <= final.confidence <= 1.0
