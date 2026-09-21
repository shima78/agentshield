"""Tests for examples/real_agent_demo.py.

Loaded directly from its file path (examples/ is not a package). Tests
must not require a real LLM API key: the LLM boundary is a `FakeProvider`
(implementing the same `AgentProvider` interface `OpenAIProvider` does) or
the demo's own `DeterministicDemoProvider` -- never a real OpenAI call.
These prove the agent (`run_agent`) is itself provider-agnostic. A small
set of real-Jev tests at the bottom mirror the pattern used elsewhere in
the suite and skip cleanly without TYPESAFE_API_KEY.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib

import pytest

from agentshield import (
    AgentProvider,
    DecisionEngine,
    Outcome,
    Policy,
    ProposedAction,
    ProviderError,
    SemanticAssessment,
    SemanticEvaluator,
    SemanticVerdict,
    build_decision_request,
)

MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "examples" / "real_agent_demo.py"


def _load_real_agent_demo():
    spec = importlib.util.spec_from_file_location("real_agent_demo", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


demo = _load_real_agent_demo()


class FakeProvider(AgentProvider):
    """Proves the agent is provider-agnostic: returns a fixed proposal, or
    raises ProviderError, with no OpenAI (or any SDK) involvement at all.
    """

    def __init__(self, proposal: ProposedAction | None = None, error: Exception | None = None):
        self._proposal = proposal
        self._error = error
        self.calls = 0

    def propose_action(self, user_request: str) -> ProposedAction:
        self.calls += 1
        if self._error is not None:
            raise self._error
        assert self._proposal is not None
        return self._proposal


class _FakeSemanticEvaluator(SemanticEvaluator):
    def __init__(self, verdict: SemanticVerdict, confidence: float = 0.8):
        self.verdict = verdict
        self.confidence = confidence
        self.calls = 0

    def assess(self, request, rule) -> SemanticAssessment:
        self.calls += 1
        return SemanticAssessment(verdict=self.verdict, confidence=self.confidence)


PRODUCTION_DELETE = ProposedAction(action="delete_database", context={"environment": "production"})
RISKY_DEPLOY = ProposedAction(
    action="deploy",
    arguments={"version": "v2.4.1"},
    context={
        "environment": "staging",
        "database_migration": True,
        "time": "friday_evening",
        "change_size": "large",
    },
)
ROUTINE_DEPLOY = ProposedAction(
    action="deploy",
    arguments={"version": "v2.4.1"},
    context={
        "environment": "staging",
        "database_migration": False,
        "time": "tuesday_morning",
        "change_size": "small",
    },
)


# --- imports correctly ---------------------------------------------------


def test_demo_module_imports_and_exposes_expected_names():
    assert hasattr(demo, "run_agent")
    assert hasattr(demo, "DeterministicDemoProvider")
    assert hasattr(demo, "POLICY")


# --- provider-agnostic: the agent never imports OpenAI --------------------


def test_demo_module_never_imports_openai_directly():
    source = MODULE_PATH.read_text(encoding="utf-8")
    for line in source.splitlines():
        stripped = line.strip()
        assert not stripped.startswith("import openai"), line
        assert "from openai" not in stripped, line


# --- DeterministicDemoProvider implements AgentProvider correctly --------


def test_deterministic_provider_production_delete():
    proposal = demo.DeterministicDemoProvider().propose_action("Delete the production database.")
    assert proposal.action == "delete_database"
    assert proposal.context == {"environment": "production"}


def test_deterministic_provider_risky_staging_deploy():
    proposal = demo.DeterministicDemoProvider().propose_action(
        "Deploy version v2.4.1 to staging. It's a large change including a "
        "database migration, and it's Friday evening."
    )
    assert proposal.action == "deploy"
    assert proposal.arguments == {"version": "v2.4.1"}
    assert proposal.context == RISKY_DEPLOY.context


def test_deterministic_provider_routine_staging_deploy():
    proposal = demo.DeterministicDemoProvider().propose_action(
        "Deploy version v2.4.1 to staging. It's a small, routine change on "
        "Tuesday morning, no database migration."
    )
    assert proposal.context == ROUTINE_DEPLOY.context


# --- DENY / REVIEW / ALLOW drive execution correctly (agent is provider-agnostic) -


def test_deny_prevents_execution(capsys):
    shield = DecisionEngine(demo.POLICY)
    provider = FakeProvider(PRODUCTION_DELETE)
    decision, executed = demo.run_agent(1, "prod delete", "irrelevant text", shield, provider)

    assert decision.outcome == Outcome.DENY
    assert executed is False
    assert "Executed (simulated)" not in capsys.readouterr().out


def test_review_prevents_execution(capsys):
    evaluator = _FakeSemanticEvaluator(SemanticVerdict.REVIEW)
    shield = DecisionEngine(demo.POLICY, semantic_evaluator=evaluator)
    provider = FakeProvider(RISKY_DEPLOY)
    decision, executed = demo.run_agent(2, "risky deploy", "irrelevant text", shield, provider)

    assert decision.outcome == Outcome.REVIEW
    assert executed is False
    assert evaluator.calls == 1
    out = capsys.readouterr().out
    assert "Executed (simulated)" not in out
    assert "requires human review" in out


def test_allow_permits_execution(capsys):
    evaluator = _FakeSemanticEvaluator(SemanticVerdict.GOOD)
    shield = DecisionEngine(demo.POLICY, semantic_evaluator=evaluator)
    provider = FakeProvider(ROUTINE_DEPLOY)
    decision, executed = demo.run_agent(3, "routine deploy", "irrelevant text", shield, provider)

    assert decision.outcome == Outcome.ALLOW
    assert executed is True
    assert "Executed (simulated): deploy" in capsys.readouterr().out


# --- AgentShield remains independent from the provider --------------------


def test_agentshield_decision_depends_only_on_the_proposed_action_not_the_provider():
    shield = DecisionEngine(demo.POLICY)
    for provider in (FakeProvider(PRODUCTION_DELETE), demo.DeterministicDemoProvider()):
        proposal = provider.propose_action("Delete the production database.")
        decision = shield.evaluate(build_decision_request(proposal))
        assert decision.outcome == Outcome.DENY


# --- Deterministic DENY/REVIEW never call Jev; ALLOW can ------------------


def test_deterministic_deny_never_calls_jev():
    evaluator = _FakeSemanticEvaluator(SemanticVerdict.GOOD)
    shield = DecisionEngine(demo.POLICY, semantic_evaluator=evaluator)
    demo.run_agent(1, "prod delete", "x", shield, FakeProvider(PRODUCTION_DELETE))
    assert evaluator.calls == 0


def test_deterministic_review_never_calls_jev():
    policy = Policy.from_dict(
        {"rules": [{"name": "r", "tool": "deploy", "outcome": "review", "risk": "high"}]}
    )
    evaluator = _FakeSemanticEvaluator(SemanticVerdict.GOOD)
    shield = DecisionEngine(policy, semantic_evaluator=evaluator)
    demo.run_agent(2, "review by policy", "x", shield, FakeProvider(ROUTINE_DEPLOY))
    assert evaluator.calls == 0


def test_deterministic_allow_can_call_jev():
    evaluator = _FakeSemanticEvaluator(SemanticVerdict.GOOD)
    shield = DecisionEngine(demo.POLICY, semantic_evaluator=evaluator)
    demo.run_agent(3, "routine deploy", "x", shield, FakeProvider(ROUTINE_DEPLOY))
    assert evaluator.calls == 1


# --- Provider selection: missing API key selects deterministic fallback --


def test_build_provider_without_api_key_selects_deterministic_fallback(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    provider, used_real_llm = demo._build_provider()
    assert used_real_llm is False
    assert isinstance(provider, demo.DeterministicDemoProvider)


# --- A configured provider failing does NOT silently fall back -----------


def test_provider_failure_does_not_execute_and_does_not_fall_back(capsys):
    shield = DecisionEngine(demo.POLICY)
    failing_provider = FakeProvider(error=ProviderError("simulated API failure"))

    decision, executed = demo.run_agent(1, "prod delete", "x", shield, failing_provider)

    assert decision is None
    assert executed is False
    out = capsys.readouterr().out
    assert "Executed (simulated)" not in out
    assert "could not propose an action" in out.lower()
    # Only ever called once -- no retry against a different provider.
    assert failing_provider.calls == 1


# --- Jev configuration (unrelated to the provider) ------------------------


def test_build_shield_without_typesafe_api_key_has_no_semantic_evaluator(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    shield = demo._build_shield()
    assert shield.semantic_evaluator is None


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
        1, "prod delete", "x", shield, FakeProvider(PRODUCTION_DELETE)
    )
    assert decision.outcome == Outcome.DENY
    assert executed is False


@pytest.mark.integration
@requires_real_jev
def test_real_jev_staging_scenarios_produce_well_formed_decisions():
    shield = demo._build_shield()
    assert shield.semantic_evaluator is not None

    for proposal in (RISKY_DEPLOY, ROUTINE_DEPLOY):
        decision, _ = demo.run_agent(2, "staging deploy", "x", shield, FakeProvider(proposal))
        # We do not hard-code Jev's real judgment -- only that the engine
        # produced a valid, well-formed decision from it.
        assert decision.outcome in (Outcome.ALLOW, Outcome.REVIEW)
        assert 0.0 <= decision.confidence <= 1.0
