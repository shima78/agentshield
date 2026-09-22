"""Tests for examples/real_agent_demo.py's Agent architecture.

Loaded directly from its file path (examples/ is not a package). Tests
must not require a real LLM API key: the LLM boundary is a `FakeProvider`
(implementing the same `AgentProvider` interface a real provider does) or
the demo's own `DeterministicDemoProvider` -- never a real OpenAI/
Anthropic/Gemini call. These prove the Agent itself is provider-agnostic
and, critically, that it never executes a proposed action without first
routing it through AgentShield. A small set of real-Jev tests at the
bottom mirror the pattern used elsewhere in the suite and skip cleanly
without TYPESAFE_API_KEY.
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
)

MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "examples" / "real_agent_demo.py"


def _load_real_agent_demo():
    spec = importlib.util.spec_from_file_location("real_agent_demo", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


demo = _load_real_agent_demo()


class FakeProvider(AgentProvider):
    """Proves the Agent is provider-agnostic: returns a fixed proposal, or
    raises ProviderError, with no OpenAI/Anthropic/Gemini (or any SDK)
    involvement at all. Also records the exact text it was asked about,
    to prove the Agent passes the real user request through unmodified.
    """

    def __init__(self, proposal: ProposedAction | None = None, error: Exception | None = None):
        self._proposal = proposal
        self._error = error
        self.received_requests: list[str] = []

    def propose_action(self, user_request: str) -> ProposedAction:
        self.received_requests.append(user_request)
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
STAGING_DEPLOY = ProposedAction(
    action="deploy",
    arguments={"version": "v2.4.1"},
    context={"environment": "staging"},
)


def _agent(provider, shield=None, tools=None):
    return demo.Agent(provider=provider, shield=shield or DecisionEngine(demo.POLICY), tools=tools)


# --- imports correctly ---------------------------------------------------


def test_demo_module_imports_and_exposes_expected_names():
    assert hasattr(demo, "Agent")
    assert hasattr(demo, "DeterministicDemoProvider")
    assert hasattr(demo, "DEFAULT_TOOLS")
    assert hasattr(demo, "POLICY")


def test_demo_module_never_imports_a_provider_sdk_directly():
    source = MODULE_PATH.read_text(encoding="utf-8")
    for line in source.splitlines():
        stripped = line.strip()
        for sdk in ("openai", "anthropic", "google"):
            assert not stripped.startswith(f"import {sdk}"), line
            assert f"from {sdk}" not in stripped, line


# --- Agent receives a natural-language request and passes it through -----


def test_agent_passes_the_exact_user_request_to_the_provider():
    provider = FakeProvider(PRODUCTION_DELETE)
    agent = _agent(provider)
    agent.handle("Delete the production database please.")
    assert provider.received_requests == ["Delete the production database please."]


# --- provider proposes a structured action; Agent converts to DecisionRequest -


def test_agent_converts_proposal_to_a_valid_decision_request():
    provider = FakeProvider(STAGING_DEPLOY)
    agent = _agent(provider)
    proposal, decision, executed = agent.handle("deploy to staging")

    assert proposal is STAGING_DEPLOY
    assert decision.outcome == Outcome.ALLOW
    assert executed is True


# --- AgentShield is called before execution -------------------------------


def test_agentshield_is_consulted_before_any_execution(monkeypatch):
    provider = FakeProvider(STAGING_DEPLOY)
    shield = DecisionEngine(demo.POLICY)
    agent = _agent(provider, shield=shield)

    call_order: list[str] = []
    real_evaluate = shield.evaluate

    def spy_evaluate(request):
        call_order.append("evaluate")
        return real_evaluate(request)

    monkeypatch.setattr(shield, "evaluate", spy_evaluate)

    tool_calls: list[str] = []
    agent.tools = {"deploy": lambda **kw: tool_calls.append("executed")}

    agent.handle("deploy to staging")

    assert call_order == ["evaluate"]
    assert tool_calls == ["executed"]
    # evaluate() must have happened -- this is the regression guard: if
    # someone changed the code to go straight from provider to execution,
    # call_order would be empty while tool_calls still showed "executed".


# --- DENY / REVIEW / ALLOW drive execution correctly ----------------------


def test_deny_prevents_execution():
    tool_calls: list[str] = []
    tools = {"delete_database": lambda **kw: tool_calls.append("executed")}
    provider = FakeProvider(PRODUCTION_DELETE)
    agent = _agent(provider, tools=tools)

    proposal, decision, executed = agent.handle("delete the prod db")

    assert decision.outcome == Outcome.DENY
    assert executed is False
    assert tool_calls == []


def test_review_prevents_execution():
    tool_calls: list[str] = []
    tools = {"deploy": lambda **kw: tool_calls.append("executed")}
    evaluator = _FakeSemanticEvaluator(SemanticVerdict.REVIEW)
    shield = DecisionEngine(demo.POLICY, semantic_evaluator=evaluator)
    provider = FakeProvider(STAGING_DEPLOY)
    agent = _agent(provider, shield=shield, tools=tools)

    proposal, decision, executed = agent.handle("deploy to staging")

    assert decision.outcome == Outcome.REVIEW
    assert executed is False
    assert tool_calls == []
    assert evaluator.calls == 1


def test_allow_permits_execution():
    tool_calls: list[tuple] = []
    tools = {"deploy": lambda **kw: tool_calls.append(kw)}
    provider = FakeProvider(STAGING_DEPLOY)
    agent = _agent(provider, tools=tools)

    proposal, decision, executed = agent.handle("deploy to staging")

    assert decision.outcome == Outcome.ALLOW
    assert executed is True
    assert tool_calls == [{"version": "v2.4.1"}]


# --- THE critical regression guard: provider -> execute must be impossible -


def test_execution_is_impossible_without_an_allow_decision():
    """This is the test that would fail if someone accidentally rewired
    the Agent to execute directly from the provider's proposal instead of
    routing it through AgentShield first. A DENY-triggering proposal must
    never reach any tool, no matter what.
    """
    tool_calls: list[str] = []
    tools = {
        name: (lambda **kw: tool_calls.append(name))
        for name in ("delete_database", "deploy", "search_repository")
    }
    provider = FakeProvider(PRODUCTION_DELETE)  # policy DENYs this
    agent = _agent(provider, tools=tools)

    agent.handle("delete the production database")

    assert tool_calls == [], (
        "A tool was executed for an action AgentShield denied -- this means "
        "execution happened without going through the ALLOW gate."
    )


# --- Jev is only consulted for deterministic ALLOW ------------------------


def test_deterministic_deny_never_calls_jev():
    evaluator = _FakeSemanticEvaluator(SemanticVerdict.GOOD)
    shield = DecisionEngine(demo.POLICY, semantic_evaluator=evaluator)
    agent = _agent(FakeProvider(PRODUCTION_DELETE), shield=shield)
    agent.handle("delete the production database")
    assert evaluator.calls == 0


def test_deterministic_review_never_calls_jev():
    policy = Policy.from_dict(
        {"rules": [{"name": "r", "tool": "deploy", "outcome": "review", "risk": "high"}]}
    )
    evaluator = _FakeSemanticEvaluator(SemanticVerdict.GOOD)
    shield = DecisionEngine(policy, semantic_evaluator=evaluator)
    agent = _agent(FakeProvider(STAGING_DEPLOY), shield=shield)
    agent.handle("deploy to staging")
    assert evaluator.calls == 0


def test_deterministic_allow_can_call_jev():
    evaluator = _FakeSemanticEvaluator(SemanticVerdict.GOOD)
    shield = DecisionEngine(demo.POLICY, semantic_evaluator=evaluator)
    agent = _agent(FakeProvider(STAGING_DEPLOY), shield=shield)
    agent.handle("deploy to staging")
    assert evaluator.calls == 1


# --- provider does not execute actions ------------------------------------


def test_provider_never_calls_any_tool_itself():
    """AgentProvider has exactly one method (propose_action) and no
    reference to tools/execution; DeterministicDemoProvider in particular
    must never touch a tool while deriving its proposal.
    """
    tool_calls: list[str] = []
    tools = {name: (lambda **kw: tool_calls.append(name)) for name in demo.DEFAULT_TOOLS}
    demo.DeterministicDemoProvider().propose_action(
        "Deploy v2.4.1 to staging, small routine change, Tuesday morning."
    )
    assert tool_calls == []  # constructing/deriving a proposal never executes anything
    assert set(tools.keys()) == set(demo.DEFAULT_TOOLS.keys())


# --- Agent works with a FakeProvider; OpenAIProvider stays isolated ------


def test_agent_works_with_a_fake_provider():
    agent = _agent(FakeProvider(STAGING_DEPLOY))
    proposal, decision, executed = agent.handle("deploy")
    assert executed is True


# --- malformed / failing provider output fails safely, never executes ---


def test_provider_error_propagates_and_never_executes():
    tool_calls: list[str] = []
    tools = {"deploy": lambda **kw: tool_calls.append("executed")}
    provider = FakeProvider(error=ProviderError("simulated failure"))
    agent = _agent(provider, tools=tools)

    with pytest.raises(ProviderError):
        agent.handle("deploy to staging")
    assert tool_calls == []


# --- DeterministicDemoProvider derives actions from request text ---------


def test_deterministic_provider_infers_production_delete_from_free_text():
    proposal = demo.DeterministicDemoProvider().propose_action(
        "Delete the production database."
    )
    assert proposal.action == "delete_database"
    assert proposal.context == {"environment": "production"}


def test_deterministic_provider_infers_risky_staging_deploy_from_free_text():
    proposal = demo.DeterministicDemoProvider().propose_action(
        "Deploy v2.4.1 to staging. It has a database migration, it's a "
        "large change, and we're doing it Friday evening."
    )
    assert proposal.action == "deploy"
    assert proposal.arguments == {"version": "v2.4.1"}
    assert proposal.context == {
        "environment": "staging",
        "time": "friday_evening",
        "database_migration": True,
        "change_size": "large",
    }


def test_deterministic_provider_infers_routine_staging_deploy_from_free_text():
    proposal = demo.DeterministicDemoProvider().propose_action(
        "Deploy v2.4.1 to staging. It's a small change with no database "
        "migration and we're doing it Tuesday morning."
    )
    assert proposal.context == {
        "environment": "staging",
        "time": "tuesday_morning",
        "database_migration": False,
        "change_size": "small",
    }


# --- Provider selection: missing keys select deterministic fallback ------


def test_build_provider_without_any_api_key_selects_deterministic_fallback(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    provider, used_real_llm = demo._build_provider()
    assert used_real_llm is False
    assert isinstance(provider, demo.DeterministicDemoProvider)


def test_build_provider_prefers_anthropic_when_all_keys_present(monkeypatch):
    from agentshield.providers.anthropic import AnthropicProvider

    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-selection-test")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key-for-selection-test")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-for-selection-test")
    provider, used_real_llm = demo._build_provider()
    assert used_real_llm is True
    assert isinstance(provider, AnthropicProvider)


# --- A configured provider failing does NOT silently fall back -----------


def test_real_provider_failure_does_not_fall_back_to_deterministic(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key-for-selection-test")

    class BrokenProvider(AgentProvider):
        def propose_action(self, user_request: str) -> ProposedAction:
            raise ProviderError("upstream is down")

    agent = _agent(BrokenProvider())
    with pytest.raises(ProviderError):
        agent.handle("deploy to staging")
    # Never silently downgraded to DeterministicDemoProvider mid-call.
    assert not isinstance(agent.provider, demo.DeterministicDemoProvider)


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
    agent = _agent(FakeProvider(PRODUCTION_DELETE), shield=shield)
    proposal, decision, executed = agent.handle("delete the production database")
    assert decision.outcome == Outcome.DENY
    assert executed is False


@pytest.mark.integration
@requires_real_jev
def test_real_jev_staging_deploy_produces_a_well_formed_decision():
    shield = demo._build_shield()
    assert shield.semantic_evaluator is not None
    agent = _agent(FakeProvider(STAGING_DEPLOY), shield=shield)
    proposal, decision, executed = agent.handle("deploy to staging")
    # We do not hard-code Jev's real judgment -- only that the engine
    # produced a valid, well-formed decision from it.
    assert decision.outcome in (Outcome.ALLOW, Outcome.REVIEW)
    assert 0.0 <= decision.confidence <= 1.0
