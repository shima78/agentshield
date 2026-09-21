"""Jev-backed semantic evaluation (optional).

Requires the optional ``jev`` dependency: ``pip install -e ".[jev]"``
(the official ``typesafe-sdk`` package, https://pypi.org/project/typesafe-sdk/).

This module implements the Core's generic ``SemanticEvaluator`` interface
(``agentshield.semantic``) using TypeSafe's Jev model, a "System One" model
that evaluates program state against typed questions and returns
structured answers (see https://docs.typesafe.ai). It is never imported by
``agentshield``'s own ``__init__.py`` or by ``DecisionEngine`` — importing
``agentshield`` (or even ``agentshield.engine``) never requires
``typesafe_sdk`` to be installed. This mirrors how ``agentshield.mcp`` is
an optional adapter the Core has no dependency on.

Only the minimal, relevant slice of policy for the one request being
evaluated is sent to Jev (the matched rule's name/outcome/risk/reason, or
a note that none matched) — never the whole policy file.
"""

from __future__ import annotations

from typing import Any, Optional

import typesafe_sdk as ts

from .policy import DecisionRequest, PolicyRule
from .semantic import SemanticAssessment, SemanticEvaluator, SemanticVerdict

_ASSESSMENT_INSTRUCTIONS = (
    "Given the proposed action, its arguments, the current context, and the "
    "applicable policy, is this a good/sensible decision?"
)

_ASSESSMENT_CRITERIA = {
    "good": (
        "The action is sensible and appropriate given the context and the "
        "applicable policy; proceeding is reasonable."
    ),
    "review": (
        "The action is plausible but risky or unusual enough that a human "
        "should look at it before it proceeds."
    ),
    "bad": (
        "The action looks like a mistake, is inappropriate for the stated "
        "context, or conflicts with the spirit of the applicable policy."
    ),
}


def _applicable_policy_summary(rule: Optional[PolicyRule]) -> dict[str, Any]:
    """The minimal, relevant slice of policy for one request — never the whole file."""
    if rule is None:
        return {"matched_rule": None, "note": "No policy rule matched; default-allow applies."}
    return {
        "matched_rule": rule.name,
        "outcome": rule.outcome.value,
        "risk": rule.risk.value,
        "reason": rule.reason,
    }


class JevSemanticEvaluator(SemanticEvaluator):
    """Asks Jev whether a proposed decision looks sensible, not just permitted."""

    def __init__(self, client: Optional[ts.TypeSafeClient] = None) -> None:
        # ts.TypeSafeClient() reads TYPESAFE_API_KEY from the environment by
        # default; no key is ever hard-coded here.
        self._client = client or ts.TypeSafeClient()

    def assess(
        self, request: DecisionRequest, rule: Optional[PolicyRule]
    ) -> SemanticAssessment:
        state = {
            "action": request.action,
            "actor": request.actor,
            "target": request.server,
            "arguments": request.arguments,
            "context": request.context,
            "applicable_policy": _applicable_policy_summary(rule),
        }
        response = self._client.system_one(
            state=state,
            questions={
                "assessment": ts.Choice(
                    instructions=_ASSESSMENT_INSTRUCTIONS,
                    criteria=_ASSESSMENT_CRITERIA,
                )
            },
        )
        answer = response.answers["assessment"]
        return SemanticAssessment(
            verdict=SemanticVerdict(answer.choice),
            confidence=answer.confidence,
            # ChoiceAnswer has no free-text explanation field in the
            # current Jev API — only choice/confidence/probabilities.
            reason=None,
        )
