import pytest
from pydantic import ValidationError

from agentshield import DecisionRequest, Policy, PolicyError, PolicyRule


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


# --- Matching -----------------------------------------------------------


def test_exact_tool_match():
    rule = PolicyRule(name="r1", tool="github.delete_repository", outcome="deny", risk="high")
    assert rule.matches(make_request(action="github.delete_repository"))
    assert not rule.matches(make_request(action="github.create_repository"))


def test_wildcard_tool_match():
    rule = PolicyRule(name="r1", tool="*.delete_*", outcome="deny", risk="high")
    assert rule.matches(make_request(action="github.delete_repository"))
    assert rule.matches(make_request(action="slack.delete_message"))
    assert not rule.matches(make_request(action="github.create_repository"))


def test_no_tool_match():
    rule = PolicyRule(name="r1", tool="github.delete_repository", outcome="deny", risk="high")
    assert not rule.matches(make_request(action="github.archive_repository"))


def test_unset_tool_matches_any_tool():
    rule = PolicyRule(name="r1", outcome="allow", risk="low")
    assert rule.matches(make_request(action="anything.at_all"))


def test_server_match():
    rule = PolicyRule(name="r1", server="github", outcome="allow", risk="low")
    assert rule.matches(make_request(server="github"))
    assert not rule.matches(make_request(server="slack"))


def test_actor_match():
    rule = PolicyRule(name="r1", actor="agent", outcome="allow", risk="low")
    assert rule.matches(make_request(actor="agent"))
    assert not rule.matches(make_request(actor="human"))


def test_context_match():
    rule = PolicyRule(
        name="r1", context={"environment": "production"}, outcome="review", risk="medium"
    )
    assert rule.matches(make_request(context={"environment": "production"}))


def test_context_mismatch():
    rule = PolicyRule(
        name="r1", context={"environment": "production"}, outcome="review", risk="medium"
    )
    assert not rule.matches(make_request(context={"environment": "staging"}))
    assert not rule.matches(make_request(context={}))


def test_multiple_context_constraints_must_all_match():
    rule = PolicyRule(
        name="r1",
        context={"environment": "production", "region": "us-east"},
        outcome="review",
        risk="medium",
    )
    assert rule.matches(
        make_request(context={"environment": "production", "region": "us-east"})
    )
    assert not rule.matches(make_request(context={"environment": "production"}))


def test_unspecified_context_fields_do_not_affect_matching():
    rule = PolicyRule(
        name="r1", context={"environment": "production"}, outcome="review", risk="medium"
    )
    assert rule.matches(
        make_request(context={"environment": "production", "extra": "value"})
    )


# --- Loading --------------------------------------------------------------


def test_policy_from_dict_valid():
    policy = Policy.from_dict(
        {
            "rules": [
                {"name": "r1", "tool": "*.read_secret", "outcome": "deny", "risk": "critical"}
            ]
        }
    )
    assert len(policy.rules) == 1
    assert policy.rules[0].name == "r1"


def test_policy_from_dict_defaults_to_empty_rules():
    policy = Policy.from_dict({})
    assert policy.rules == []


def test_policy_invalid_outcome_raises():
    with pytest.raises(ValidationError):
        Policy.from_dict(
            {"rules": [{"name": "r1", "tool": "x", "outcome": "maybe", "risk": "low"}]}
        )


def test_policy_invalid_risk_raises():
    with pytest.raises(ValidationError):
        Policy.from_dict(
            {"rules": [{"name": "r1", "tool": "x", "outcome": "deny", "risk": "extreme"}]}
        )


def test_policy_missing_name_raises():
    with pytest.raises(ValidationError):
        Policy.from_dict({"rules": [{"tool": "x", "outcome": "deny", "risk": "low"}]})


def test_policy_missing_outcome_raises():
    with pytest.raises(ValidationError):
        Policy.from_dict({"rules": [{"name": "r1", "tool": "x", "risk": "low"}]})


def test_policy_missing_risk_raises():
    with pytest.raises(ValidationError):
        Policy.from_dict({"rules": [{"name": "r1", "tool": "x", "outcome": "deny"}]})


def test_policy_malformed_rules_type_raises():
    with pytest.raises(ValidationError):
        Policy.from_dict({"rules": "not-a-list"})


def test_policy_from_yaml_valid(tmp_path):
    yaml_content = """
rules:
  - name: block-secret-access
    tool: "*.read_secret"
    outcome: deny
    risk: critical
    reason: "Access to secrets is blocked."
"""
    path = tmp_path / "policy.yaml"
    path.write_text(yaml_content, encoding="utf-8")
    policy = Policy.from_yaml(str(path))
    assert policy.rules[0].name == "block-secret-access"
    assert policy.rules[0].outcome == "deny"


def test_policy_from_yaml_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        Policy.from_yaml("does-not-exist.yaml")


def test_policy_from_yaml_malformed_raises_policy_error(tmp_path):
    path = tmp_path / "bad.yaml"
    path.write_text("rules: [unclosed", encoding="utf-8")
    with pytest.raises(PolicyError):
        Policy.from_yaml(str(path))


def test_policy_from_yaml_empty_file_is_empty_policy(tmp_path):
    path = tmp_path / "empty.yaml"
    path.write_text("", encoding="utf-8")
    policy = Policy.from_yaml(str(path))
    assert policy.rules == []
