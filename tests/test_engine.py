from agentshield import (
    DecisionEngine,
    DecisionRequest,
    Outcome,
    Policy,
    RiskLevel,
)
from agentshield.engine import DEFAULT_ALLOW_REASON


def make_request(**overrides):
    defaults = dict(
        actor="agent",
        server="github",
        action="github.delete_repository",
        arguments={},
        context={},
    )
    defaults.update(overrides)
    return DecisionRequest(**defaults)


# --- Defaults ---------------------------------------------------------


def test_default_allow_when_no_rule_matches():
    engine = DecisionEngine(Policy(rules=[]))
    decision = engine.evaluate(make_request())
    assert decision.outcome == Outcome.ALLOW
    assert decision.allowed is True
    assert decision.risk == RiskLevel.LOW
    assert decision.confidence == 1.0
    assert decision.rule is None
    assert decision.reason == DEFAULT_ALLOW_REASON


def test_default_allow_when_rules_exist_but_none_match():
    policy = Policy.from_dict(
        {
            "rules": [
                {
                    "name": "r1",
                    "tool": "slack.delete_message",
                    "outcome": "deny",
                    "risk": "high",
                }
            ]
        }
    )
    engine = DecisionEngine(policy)
    decision = engine.evaluate(make_request(action="github.create_repository"))
    assert decision.outcome == Outcome.ALLOW
    assert decision.rule is None


# --- Precedence ---------------------------------------------------------


def test_exact_beats_wildcard():
    policy = Policy.from_dict(
        {
            "rules": [
                {"name": "wildcard-deny", "tool": "*.delete_*", "outcome": "deny", "risk": "high"},
                {
                    "name": "exact-allow",
                    "tool": "github.delete_repository",
                    "outcome": "allow",
                    "risk": "low",
                },
            ]
        }
    )
    engine = DecisionEngine(policy)
    decision = engine.evaluate(make_request(action="github.delete_repository"))
    assert decision.rule == "exact-allow"
    assert decision.outcome == Outcome.ALLOW


def test_wildcard_beats_unconstrained():
    policy = Policy.from_dict(
        {
            "rules": [
                {"name": "unconstrained-allow", "outcome": "allow", "risk": "low"},
                {"name": "wildcard-deny", "tool": "*.delete_*", "outcome": "deny", "risk": "high"},
            ]
        }
    )
    engine = DecisionEngine(policy)
    decision = engine.evaluate(make_request(action="github.delete_repository"))
    assert decision.rule == "wildcard-deny"
    assert decision.outcome == Outcome.DENY


def test_more_context_constraints_wins():
    policy = Policy.from_dict(
        {
            "rules": [
                {
                    "name": "broad",
                    "tool": "merge_pull_request",
                    "outcome": "allow",
                    "risk": "low",
                },
                {
                    "name": "narrow",
                    "tool": "merge_pull_request",
                    "context": {"environment": "production"},
                    "outcome": "review",
                    "risk": "high",
                },
            ]
        }
    )
    engine = DecisionEngine(policy)
    decision = engine.evaluate(
        make_request(action="merge_pull_request", context={"environment": "production"})
    )
    assert decision.rule == "narrow"
    assert decision.outcome == Outcome.REVIEW


def test_more_specific_actor_server_wins():
    policy = Policy.from_dict(
        {
            "rules": [
                {
                    "name": "generic",
                    "tool": "merge_pull_request",
                    "outcome": "allow",
                    "risk": "low",
                },
                {
                    "name": "specific",
                    "tool": "merge_pull_request",
                    "server": "github",
                    "outcome": "deny",
                    "risk": "high",
                },
            ]
        }
    )
    engine = DecisionEngine(policy)
    decision = engine.evaluate(make_request(action="merge_pull_request", server="github"))
    assert decision.rule == "specific"
    assert decision.outcome == Outcome.DENY


def test_earlier_rule_wins_on_full_tie():
    policy = Policy.from_dict(
        {
            "rules": [
                {
                    "name": "first",
                    "tool": "merge_pull_request",
                    "outcome": "allow",
                    "risk": "low",
                },
                {
                    "name": "second",
                    "tool": "merge_pull_request",
                    "outcome": "deny",
                    "risk": "high",
                },
            ]
        }
    )
    engine = DecisionEngine(policy)
    decision = engine.evaluate(make_request(action="merge_pull_request"))
    assert decision.rule == "first"
    assert decision.outcome == Outcome.ALLOW


def test_precedence_is_independent_of_rule_reordering_by_content():
    # Same rules, reversed order: precedence must follow the *earlier rule*
    # tiebreaker relative to each policy's own list, not any external order.
    policy_a = Policy.from_dict(
        {
            "rules": [
                {"name": "first", "tool": "x", "outcome": "allow", "risk": "low"},
                {"name": "second", "tool": "x", "outcome": "deny", "risk": "high"},
            ]
        }
    )
    policy_b = Policy.from_dict(
        {
            "rules": [
                {"name": "second", "tool": "x", "outcome": "deny", "risk": "high"},
                {"name": "first", "tool": "x", "outcome": "allow", "risk": "low"},
            ]
        }
    )
    request = make_request(action="x")
    assert DecisionEngine(policy_a).evaluate(request).rule == "first"
    assert DecisionEngine(policy_b).evaluate(request).rule == "second"


# --- Safety: DENY cannot be overridden by a lower-precedence rule ------


def test_deny_not_overridden_by_lower_precedence_allow():
    policy = Policy.from_dict(
        {
            "rules": [
                {
                    "name": "exact-deny",
                    "tool": "github.delete_repository",
                    "outcome": "deny",
                    "risk": "critical",
                },
                {
                    "name": "wildcard-allow",
                    "tool": "*.delete_*",
                    "outcome": "allow",
                    "risk": "low",
                },
            ]
        }
    )
    engine = DecisionEngine(policy)
    decision = engine.evaluate(make_request(action="github.delete_repository"))
    assert decision.outcome == Outcome.DENY
    assert decision.allowed is False
    assert decision.rule == "exact-deny"


def test_allow_not_overridden_by_lower_precedence_deny():
    policy = Policy.from_dict(
        {
            "rules": [
                {
                    "name": "exact-allow",
                    "tool": "github.delete_repository",
                    "outcome": "allow",
                    "risk": "low",
                },
                {
                    "name": "wildcard-deny",
                    "tool": "*.delete_*",
                    "outcome": "deny",
                    "risk": "critical",
                },
            ]
        }
    )
    engine = DecisionEngine(policy)
    decision = engine.evaluate(make_request(action="github.delete_repository"))
    assert decision.outcome == Outcome.ALLOW
    assert decision.rule == "exact-allow"


# --- Engine does not mutate its inputs ---------------------------------


def test_engine_does_not_mutate_policy_or_request():
    policy = Policy.from_dict(
        {"rules": [{"name": "r1", "tool": "x", "outcome": "allow", "risk": "low"}]}
    )
    engine = DecisionEngine(policy)
    request = make_request(action="x")
    engine.evaluate(request)
    assert engine.policy is policy
    assert request.action == "x"
