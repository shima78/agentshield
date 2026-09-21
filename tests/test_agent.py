"""Tests for agentshield.agent: the provider-agnostic ProposedAction/
AgentProvider interface. No provider-specific (openai) dependency.

Proves the agent-level flow end to end using a fake provider:

    provider -> ProposedAction -> DecisionRequest -> AgentShield -> Outcome
"""

import pytest

from agentshield import (
    AgentProvider,
    DecisionEngine,
    Outcome,
    Policy,
    ProposedAction,
    build_decision_request,
)

POLICY = Policy.from_dict(
    {
        "rules": [
            {
                "name": "deny-delete",
                "tool": "delete_database",
                "outcome": "deny",
                "risk": "critical",
            },
            {"name": "allow-deploy", "tool": "deploy", "outcome": "allow", "risk": "low"},
        ]
    }
)


class FakeProvider(AgentProvider):
    """A minimal AgentProvider stand-in for tests."""

    def __init__(self, proposal: ProposedAction):
        self._proposal = proposal
        self.calls = 0

    def propose_action(self, user_request: str) -> ProposedAction:
        self.calls += 1
        return self._proposal


# --- ProposedAction ---------------------------------------------------


def test_proposed_action_defaults_arguments_and_context_to_empty_dicts():
    proposal = ProposedAction(action="deploy")
    assert proposal.arguments == {}
    assert proposal.context == {}


def test_proposed_action_is_frozen():
    proposal = ProposedAction(action="deploy")
    with pytest.raises(Exception):
        proposal.action = "other"  # type: ignore[misc]


# --- AgentProvider is an interface, not directly instantiable -----------


def test_agent_provider_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        AgentProvider()  # type: ignore[abstract]


def test_fake_provider_implements_the_interface():
    proposal = ProposedAction(action="deploy", arguments={"version": "1.0"})
    provider = FakeProvider(proposal)
    assert provider.propose_action("do the thing") is proposal
    assert provider.calls == 1


# --- build_decision_request: the one conversion step ---------------------


def test_build_decision_request_maps_fields_correctly():
    proposal = ProposedAction(
        action="deploy",
        arguments={"version": "v2.4.1"},
        context={"environment": "staging"},
    )
    request = build_decision_request(proposal)

    assert request.action == "deploy"
    assert request.actor == "ai-agent"
    assert request.arguments == {"version": "v2.4.1"}
    assert request.context == {"environment": "staging"}


def test_build_decision_request_accepts_a_custom_actor():
    proposal = ProposedAction(action="deploy")
    request = build_decision_request(proposal, actor="release-bot")
    assert request.actor == "release-bot"


# --- End-to-end: provider -> ProposedAction -> DecisionRequest -> AgentShield


def test_provider_to_decision_flow_deny():
    provider = FakeProvider(ProposedAction(action="delete_database"))
    engine = DecisionEngine(POLICY)

    proposal = provider.propose_action("delete the database")
    request = build_decision_request(proposal)
    decision = engine.evaluate(request)

    assert decision.outcome == Outcome.DENY
    assert decision.allowed is False


def test_provider_to_decision_flow_allow():
    provider = FakeProvider(ProposedAction(action="deploy", context={"environment": "staging"}))
    engine = DecisionEngine(POLICY)

    proposal = provider.propose_action("deploy to staging")
    request = build_decision_request(proposal)
    decision = engine.evaluate(request)

    assert decision.outcome == Outcome.ALLOW
    assert decision.allowed is True


def test_provider_to_decision_flow_review_via_no_matching_deny_rule():
    policy = Policy.from_dict(
        {"rules": [{"name": "r", "tool": "deploy", "outcome": "review", "risk": "high"}]}
    )
    provider = FakeProvider(ProposedAction(action="deploy"))
    engine = DecisionEngine(policy)

    proposal = provider.propose_action("deploy")
    decision = engine.evaluate(build_decision_request(proposal))

    assert decision.outcome == Outcome.REVIEW
    assert decision.allowed is False
