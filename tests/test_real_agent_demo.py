"""Tests for examples/real_agent_demo.py.

Loaded directly from its file path (examples/ is not a package). Tests
must not require a real LLM API key: the LLM boundary
(`propose_action_with_llm`) is exercised either via its deterministic
fallback (no client) or a mocked client object -- never a real OpenAI
call. A small set of real-Jev tests at the bottom mirror the pattern used
elsewhere in the suite and skip cleanly without TYPESAFE_API_KEY.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from agentshield import (
    DecisionEngine,
    DecisionRequest,
    Outcome,
    Policy,
    SemanticAssessment,
    SemanticEvaluator,
    SemanticVerdict,
)

MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "examples" / "real_agent_demo.py"


def _load_real_agent_demo():
    spec = importlib.util.spec_from_file_location("real_agent_demo", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


demo = _load_real_agent_demo()


class _FakeSemanticEvaluator(SemanticEvaluator):
    def __init__(self, verdict: SemanticVerdict, confidence: float = 0.8):
        self.verdict = verdict
        self.confidence = confidence
        self.calls = 0

    def assess(self, request: DecisionRequest, rule) -> SemanticAssessment:
        self.calls += 1
        return SemanticAssessment(verdict=self.verdict, confidence=self.confidence)


def _mock_openai_client(action_dict: dict) -> MagicMock:
    """A stand-in for an openai.OpenAI() client returning a canned JSON reply."""
    import json

    client = MagicMock()
    message = SimpleNamespace(content=json.dumps(action_dict))
    choice = SimpleNamespace(message=message)
    client.chat.completions.create.return_value = SimpleNamespace(choices=[choice])
    return client


# --- imports correctly ---------------------------------------------------


def test_demo_module_imports_and_exposes_expected_names():
    assert hasattr(demo, "propose_action_with_llm")
    assert hasattr(demo, "build_decision_request")
    assert hasattr(demo, "execute_action")
    assert hasattr(demo, "run_agent")
    assert hasattr(demo, "POLICY")


# --- Agent proposes an action and constructs a valid DecisionRequest ----


def test_fallback_proposal_for_production_delete():
    proposed, used_llm = demo.propose_action_with_llm(
        "Delete the production database.", client=None
    )
    assert used_llm is False
    assert proposed["action"] == "delete_database"
    assert proposed["context"]["environment"] == "production"

    request = demo.build_decision_request(proposed)
    assert isinstance(request, DecisionRequest)
    assert request.action == "delete_database"
    assert request.actor == "ai-agent"
    assert request.context == {"environment": "production"}


def test_fallback_proposal_for_risky_staging_deploy():
    proposed, used_llm = demo.propose_action_with_llm(
        "Deploy version v2.4.1 to staging. It's a large change including a "
        "database migration, and it's Friday evening.",
        client=None,
    )
    assert used_llm is False
    assert proposed["action"] == "deploy"
    assert proposed["arguments"] == {"version": "v2.4.1"}
    assert proposed["context"] == {
        "environment": "staging",
        "time": "friday_evening",
        "database_migration": True,
        "change_size": "large",
    }


def test_fallback_proposal_for_routine_staging_deploy():
    proposed, used_llm = demo.propose_action_with_llm(
        "Deploy version v2.4.1 to staging. It's a small, routine change on "
        "Tuesday morning, no database migration.",
        client=None,
    )
    assert used_llm is False
    assert proposed["context"] == {
        "environment": "staging",
        "time": "tuesday_morning",
        "database_migration": False,
        "change_size": "small",
    }


def test_proposal_via_mocked_llm_client_is_used_and_labeled():
    action_dict = {"action": "deploy", "arguments": {"version": "v9.9.9"}, "context": {}}
    client = _mock_openai_client(action_dict)

    proposed, used_llm = demo.propose_action_with_llm("anything", client=client)

    assert used_llm is True
    assert proposed == action_dict
    client.chat.completions.create.assert_called_once()


# --- DENY / REVIEW / ALLOW drive execution correctly ---------------------


def test_deny_prevents_execution(capsys):
    shield = DecisionEngine(demo.POLICY)  # no semantic evaluator
    decision, executed = demo.run_agent(
        1, "prod delete", "Delete the production database.", shield
    )
    assert decision.outcome == Outcome.DENY
    assert executed is False
    out = capsys.readouterr().out
    assert "Executed (simulated)" not in out


def test_review_prevents_execution(capsys):
    evaluator = _FakeSemanticEvaluator(SemanticVerdict.REVIEW)
    shield = DecisionEngine(demo.POLICY, semantic_evaluator=evaluator)
    decision, executed = demo.run_agent(
        2,
        "risky deploy",
        "Deploy version v2.4.1 to staging. It's a large change including a "
        "database migration, and it's Friday evening.",
        shield,
    )
    assert decision.outcome == Outcome.REVIEW
    assert executed is False
    assert evaluator.calls == 1
    out = capsys.readouterr().out
    assert "Executed (simulated)" not in out
    assert "requires human review" in out


def test_allow_permits_execution(capsys):
    evaluator = _FakeSemanticEvaluator(SemanticVerdict.GOOD)
    shield = DecisionEngine(demo.POLICY, semantic_evaluator=evaluator)
    decision, executed = demo.run_agent(
        3,
        "routine deploy",
        "Deploy version v2.4.1 to staging. It's a small, routine change on "
        "Tuesday morning, no database migration.",
        shield,
    )
    assert decision.outcome == Outcome.ALLOW
    assert executed is True
    out = capsys.readouterr().out
    assert "Executed (simulated): deploy" in out


# --- AgentShield remains independent from the LLM -------------------------


def test_agentshield_decision_depends_only_on_the_proposed_action_dict():
    # A decision is reachable purely from a dict of the right shape,
    # regardless of whether it came from a real LLM, a mock, or the
    # deterministic fallback -- AgentShield never touches the LLM layer.
    proposed = {
        "action": "delete_database",
        "arguments": {},
        "context": {"environment": "production"},
    }
    request = demo.build_decision_request(proposed)
    engine = DecisionEngine(demo.POLICY)
    decision = engine.evaluate(request)
    assert decision.outcome == Outcome.DENY


def test_execute_action_never_called_for_a_deny_flow(capsys):
    shield = DecisionEngine(demo.POLICY)
    demo.run_agent(1, "prod delete", "Delete the production database.", shield)
    out = capsys.readouterr().out
    assert "Executed (simulated)" not in out


# --- Deterministic DENY/REVIEW never call Jev; ALLOW can ------------------


def test_deterministic_deny_never_calls_jev():
    evaluator = _FakeSemanticEvaluator(SemanticVerdict.GOOD)
    shield = DecisionEngine(demo.POLICY, semantic_evaluator=evaluator)
    demo.run_agent(1, "prod delete", "Delete the production database.", shield)
    assert evaluator.calls == 0


def test_deterministic_review_never_calls_jev():
    policy = Policy.from_dict(
        {"rules": [{"name": "r", "tool": "deploy", "outcome": "review", "risk": "high"}]}
    )
    evaluator = _FakeSemanticEvaluator(SemanticVerdict.GOOD)
    shield = DecisionEngine(policy, semantic_evaluator=evaluator)
    demo.run_agent(
        2,
        "review by policy",
        "Deploy version v2.4.1 to staging. It's a small, routine change on "
        "Tuesday morning, no database migration.",
        shield,
    )
    assert evaluator.calls == 0


def test_deterministic_allow_can_call_jev():
    evaluator = _FakeSemanticEvaluator(SemanticVerdict.GOOD)
    shield = DecisionEngine(demo.POLICY, semantic_evaluator=evaluator)
    demo.run_agent(
        3,
        "routine deploy",
        "Deploy version v2.4.1 to staging. It's a small, routine change on "
        "Tuesday morning, no database migration.",
        shield,
    )
    assert evaluator.calls == 1


# --- Deterministic-only mode: no Jev configured ---------------------------


def test_build_shield_without_typesafe_api_key_has_no_semantic_evaluator(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    shield = demo._build_shield()
    assert shield.semantic_evaluator is None


def test_build_openai_client_without_api_key_is_none(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert demo._build_openai_client() is None


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
def test_real_jev_deny_scenario_is_final():
    shield = demo._build_shield()
    assert shield.semantic_evaluator is not None
    decision, executed = demo.run_agent(
        1, "prod delete", "Delete the production database.", shield
    )
    assert decision.outcome == Outcome.DENY
    assert executed is False


@pytest.mark.integration
@requires_real_jev
def test_real_jev_staging_scenarios_produce_well_formed_decisions():
    shield = demo._build_shield()
    assert shield.semantic_evaluator is not None

    for request_text in (
        "Deploy version v2.4.1 to staging. It's a large change including a "
        "database migration, and it's Friday evening.",
        "Deploy version v2.4.1 to staging. It's a small, routine change on "
        "Tuesday morning, no database migration.",
    ):
        decision, _ = demo.run_agent(2, "staging deploy", request_text, shield)
        # We do not hard-code Jev's real judgment -- only that the engine
        # produced a valid, well-formed decision from it.
        assert decision.outcome in (Outcome.ALLOW, Outcome.REVIEW)
        assert 0.0 <= decision.confidence <= 1.0
