"""Tests for the generic decision API, using non-MCP-flavored actions.

The point of this file is to demonstrate and lock in that the Core's
``DecisionEngine``/``DecisionRequest`` are genuinely generic: nothing here
is an MCP "tool" call. Actions like "send_email" or "deploy" exercise the
exact same policy engine used by the MCP adapter, with the exact same
semantics (ALLOW/REVIEW/DENY, risk levels, precedence, wildcard and
context matching, audit events, no-match default).
"""

from agentshield import (
    AuditLog,
    Decision,
    DecisionEngine,
    DecisionRequest,
    Outcome,
    Policy,
    RiskLevel,
)
from agentshield.engine import DEFAULT_ALLOW_REASON

GENERIC_POLICY = Policy.from_dict(
    {
        "rules": [
            {
                "name": "allow-read-status",
                "tool": "read_status",
                "outcome": "allow",
                "risk": "low",
                "reason": "Reading status is harmless.",
            },
            {
                "name": "block-wire-transfer",
                "tool": "wire_transfer",
                "outcome": "deny",
                "risk": "critical",
                "reason": "Wire transfers are never allowed automatically.",
            },
            {
                "name": "review-production-deploy",
                "tool": "deploy",
                "context": {"environment": "production"},
                "outcome": "review",
                "risk": "high",
                "reason": "Production deploys require human approval.",
            },
            {
                "name": "block-bulk-mail-actions",
                "tool": "*_bulk_email",
                "outcome": "deny",
                "risk": "medium",
                "reason": "Bulk email actions are blocked by default.",
            },
        ]
    }
)


def make_request(**overrides) -> DecisionRequest:
    defaults = dict(actor="agent", action="read_status", arguments={}, context={})
    defaults.update(overrides)
    return DecisionRequest(**defaults)


# --- 1-3: matching rule for each outcome -----------------------------------


def test_matching_rule_allows():
    shield = DecisionEngine(GENERIC_POLICY)
    decision = shield.evaluate(make_request(action="read_status"))
    assert decision.outcome == Outcome.ALLOW
    assert decision.allowed is True
    assert decision.rule == "allow-read-status"


def test_matching_rule_denies():
    shield = DecisionEngine(GENERIC_POLICY)
    decision = shield.evaluate(make_request(action="wire_transfer"))
    assert decision.outcome == Outcome.DENY
    assert decision.allowed is False
    assert decision.rule == "block-wire-transfer"


def test_matching_rule_reviews():
    shield = DecisionEngine(GENERIC_POLICY)
    decision = shield.evaluate(
        make_request(action="deploy", context={"environment": "production"})
    )
    assert decision.outcome == Outcome.REVIEW
    assert decision.allowed is False
    assert decision.rule == "review-production-deploy"


# --- 4: risk propagation -----------------------------------------------


def test_risk_propagates_from_matched_rule():
    shield = DecisionEngine(GENERIC_POLICY)
    decision = shield.evaluate(make_request(action="wire_transfer"))
    assert decision.risk == RiskLevel.CRITICAL

    decision2 = shield.evaluate(
        make_request(action="deploy", context={"environment": "production"})
    )
    assert decision2.risk == RiskLevel.HIGH


# --- 5: context matching -------------------------------------------------


def test_context_matching_distinguishes_environments():
    shield = DecisionEngine(GENERIC_POLICY)
    prod_decision = shield.evaluate(
        make_request(action="deploy", context={"environment": "production"})
    )
    staging_decision = shield.evaluate(
        make_request(action="deploy", context={"environment": "staging"})
    )
    assert prod_decision.outcome == Outcome.REVIEW
    assert staging_decision.outcome == Outcome.ALLOW  # no rule for staging -> default
    assert staging_decision.rule is None


# --- 6: wildcard action matching -----------------------------------------


def test_wildcard_action_matching():
    shield = DecisionEngine(GENERIC_POLICY)
    decision = shield.evaluate(make_request(action="send_bulk_email"))
    assert decision.outcome == Outcome.DENY
    assert decision.rule == "block-bulk-mail-actions"

    unrelated = shield.evaluate(make_request(action="send_single_email"))
    assert unrelated.outcome == Outcome.ALLOW
    assert unrelated.rule is None


# --- 7: no matching rule --------------------------------------------------


def test_no_matching_rule_defaults_to_allow():
    shield = DecisionEngine(GENERIC_POLICY)
    decision = shield.evaluate(make_request(action="completely_unrelated_action"))
    assert decision.outcome == Outcome.ALLOW
    assert decision.risk == RiskLevel.LOW
    assert decision.confidence == 1.0
    assert decision.rule is None
    assert decision.reason == DEFAULT_ALLOW_REASON


# --- 8: audit event creation -----------------------------------------------


def test_audit_event_creation_for_generic_actions():
    shield = DecisionEngine(GENERIC_POLICY)
    audit = AuditLog()

    request = make_request(action="wire_transfer")
    decision = shield.evaluate(request)
    event = audit.record(request, decision)

    assert event.request.action == "wire_transfer"
    assert event.decision.outcome == Outcome.DENY
    assert audit.events == [event]


# --- 9: generic action evaluation without any MCP concept ------------------


def test_generic_actions_have_no_mcp_flavored_semantics():
    """DecisionRequest/DecisionEngine work identically for actions that are
    not MCP tool calls at all — an email send, a deploy, a bank transfer.
    """
    shield = DecisionEngine(GENERIC_POLICY)

    email_decision = shield.evaluate(
        DecisionRequest(actor="agent", action="send_marketing_bulk_email", arguments={})
    )
    deploy_decision = shield.evaluate(
        DecisionRequest(
            actor="ci-bot",
            action="deploy",
            context={"environment": "production"},
            arguments={"service": "billing"},
        )
    )
    assert isinstance(email_decision, Decision)
    assert isinstance(deploy_decision, Decision)
    assert email_decision.outcome == Outcome.DENY
    assert deploy_decision.outcome == Outcome.REVIEW


# --- Backward-compat aliases ------------------------------------------


def test_deprecated_aliases_point_to_the_new_names():
    import agentshield

    assert agentshield.AuthorizationEngine is agentshield.DecisionEngine
    assert agentshield.AuthorizationRequest is agentshield.DecisionRequest
