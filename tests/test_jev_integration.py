"""Real external integration tests against the live Jev API.

Unlike test_jev_evaluator.py (mocked, offline), these make a genuine HTTP
call to https://api.typesafe.ai and require:

* The optional ``typesafe_sdk`` package installed (``pip install -e ".[jev]"``).
* ``TYPESAFE_API_KEY`` set in this process's environment.

Both are checked up front; if either is missing, every test in this
module is skipped with a clear reason. Plain `pytest` therefore still
passes completely offline and credential-free. To run these deliberately:

    pytest -m integration

To exclude them explicitly (e.g. in CI):

    pytest -m "not integration"
"""

from __future__ import annotations

import os

import pytest

try:
    import typesafe_sdk as ts

    _TYPESAFE_SDK_AVAILABLE = True
except ImportError:
    _TYPESAFE_SDK_AVAILABLE = False

from agentshield import DecisionEngine, DecisionRequest, Outcome, Policy, SemanticVerdict

TYPESAFE_API_KEY_ENV = "TYPESAFE_API_KEY"


def _prerequisites_missing_reason() -> str | None:
    if not _TYPESAFE_SDK_AVAILABLE:
        return "typesafe_sdk is not installed (pip install -e \".[jev]\")."
    if not os.environ.get(TYPESAFE_API_KEY_ENV):
        return f"{TYPESAFE_API_KEY_ENV} is not set."
    return None


pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        _prerequisites_missing_reason() is not None,
        reason=_prerequisites_missing_reason() or "",
    ),
]

POLICY = Policy.from_dict(
    {
        "rules": [
            {
                "name": "deny-delete",
                "tool": "delete_repository",
                "outcome": "deny",
                "risk": "critical",
            },
        ]
    }
)


def test_real_jev_call_returns_a_usable_semantic_assessment():
    from agentshield.jev import JevSemanticEvaluator

    evaluator = JevSemanticEvaluator()
    request = DecisionRequest(
        actor="release-agent",
        action="deploy",
        context={
            "environment": "production",
            "time": "friday_evening",
            "database_migration": True,
        },
    )
    assessment = evaluator.assess(request, None)

    assert assessment.verdict in (
        SemanticVerdict.GOOD,
        SemanticVerdict.REVIEW,
        SemanticVerdict.BAD,
    )
    assert 0.0 <= assessment.confidence <= 1.0


def test_real_jev_semantic_evaluation_through_the_engine_for_a_clearly_risky_action():
    from agentshield.jev import JevSemanticEvaluator

    engine = DecisionEngine(POLICY, semantic_evaluator=JevSemanticEvaluator())
    decision = engine.evaluate(
        DecisionRequest(
            actor="release-agent",
            action="run_untested_database_migration",
            context={
                "environment": "production",
                "time": "friday_evening",
                "confirmed_by_human": False,
            },
        )
    )
    # Deterministic policy has no opinion on this action, so it defaults
    # to ALLOW; a real semantic judgment of a Friday-evening, unconfirmed
    # production DB migration is not asserted to be any specific verdict
    # here (that's Jev's call, not ours to hard-code), but the decision
    # must be a real, well-formed Decision either way.
    assert decision.outcome in (Outcome.ALLOW, Outcome.REVIEW)
    assert decision.confidence is not None


def test_real_jev_deny_is_never_overridden():
    from agentshield.jev import JevSemanticEvaluator

    engine = DecisionEngine(POLICY, semantic_evaluator=JevSemanticEvaluator())
    decision = engine.evaluate(
        DecisionRequest(actor="agent", action="delete_repository", context={})
    )
    assert decision.outcome == Outcome.DENY
    assert decision.allowed is False
