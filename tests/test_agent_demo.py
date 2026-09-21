"""Minimal tests for examples/agent_demo.py.

Loaded directly from its file path (examples/ is not a package) so these
tests exercise the exact demo code, not a reimplementation of it.
"""

import importlib.util
import pathlib

from agentshield import Decision, DecisionEngine, DecisionRequest, Outcome, Policy

MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "examples" / "agent_demo.py"


def _load_agent_demo():
    spec = importlib.util.spec_from_file_location("agent_demo", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


agent_demo = _load_agent_demo()


# --- imports correctly ---------------------------------------------------


def test_demo_module_imports_and_exposes_expected_names():
    assert hasattr(agent_demo, "propose_deploy")
    assert hasattr(agent_demo, "_build_shield")
    assert hasattr(agent_demo, "POLICY")


# --- DecisionRequest is constructed correctly ----------------------------


def test_build_request_constructs_a_valid_decision_request():
    context = {"environment": "production", "database_migration": True}
    request = agent_demo._build_request("2.4.1", context)

    assert isinstance(request, DecisionRequest)
    assert request.action == "deploy"
    assert request.actor == "release-agent"
    assert request.context == context
    assert request.arguments == {"version": "2.4.1"}


# --- AgentShield is called before execution; outcome drives execution ---


def test_allow_permits_the_simulated_action(capsys):
    engine = DecisionEngine(Policy(rules=[]))  # no rules -> default allow
    decision, executed = agent_demo.propose_deploy(engine, "1.0.0", {"environment": "staging"})

    assert isinstance(decision, Decision)
    assert decision.outcome == Outcome.ALLOW
    assert executed is True
    assert "Deploying version 1.0.0" in capsys.readouterr().out


def test_review_prevents_the_simulated_action(capsys):
    engine = DecisionEngine(agent_demo.POLICY)
    decision, executed = agent_demo.propose_deploy(
        engine, "2.4.1", {"environment": "production", "database_migration": True}
    )

    assert decision.outcome == Outcome.REVIEW
    assert executed is False
    out = capsys.readouterr().out
    assert "Deploying version" not in out
    assert "not executed" in out


def test_deny_prevents_the_simulated_action(capsys):
    policy = Policy.from_dict(
        {
            "rules": [
                {"name": "block-deploy", "tool": "deploy", "outcome": "deny", "risk": "critical"}
            ]
        }
    )
    engine = DecisionEngine(policy)
    decision, executed = agent_demo.propose_deploy(engine, "2.4.1", {"environment": "production"})

    assert decision.outcome == Outcome.DENY
    assert executed is False
    out = capsys.readouterr().out
    assert "Deploying version" not in out
    assert "not executed" in out


# --- demo still works without Jev installed/credentials ------------------


def test_build_shield_without_typesafe_api_key_has_no_semantic_evaluator(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    shield, jev_enabled = agent_demo._build_shield()

    assert jev_enabled is False
    assert shield.semantic_evaluator is None
    # Deterministic policy still fully works.
    decision = shield.evaluate(
        DecisionRequest(action="deploy", actor="agent", context={"environment": "production"})
    )
    assert decision.outcome == Outcome.REVIEW
