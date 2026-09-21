"""Minimal example: combining deterministic policy with optional semantic
evaluation via Jev (TypeSafe's System One model).

This demonstrates that AgentShield can consider the requested action,
context, the applicable policy, and a semantic assessment of whether the
action is actually a *sensible* one to take right now -- not just whether
it is technically permitted.

This is a decision, not an enforcement mechanism or a security guarantee:
AgentShield does not execute anything, and the agent/application remains
responsible for deciding whether and how to act on the returned Decision.

Prerequisites:

    pip install -e ".[jev]"
    export TYPESAFE_API_KEY=your_key_here   # never committed

Run:

    python examples/jev_example.py

Makes a real call to the live Jev API (https://api.typesafe.ai) -- no
mocking. If TYPESAFE_API_KEY is not set, this exits with a clear message
instead of pretending to succeed.
"""

from __future__ import annotations

import os
import sys

from agentshield import DecisionEngine, DecisionRequest, Outcome, Policy

# A deliberately tiny deterministic policy: nothing here explicitly
# matches "deploy", so the deterministic layer would default-allow it.
# Whether that's actually a *good idea* right now is exactly the question
# semantic evaluation adds on top.
POLICY = Policy.from_dict({"rules": []})


def main() -> None:
    if not os.environ.get("TYPESAFE_API_KEY"):
        print(
            "TYPESAFE_API_KEY is not set -- this example makes a real call "
            "to the live Jev API and needs it. See the module docstring.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    from agentshield.jev import JevSemanticEvaluator

    shield = DecisionEngine(POLICY, semantic_evaluator=JevSemanticEvaluator())

    request = DecisionRequest(
        action="deploy",
        actor="release-agent",
        context={
            "environment": "production",
            "time": "friday_evening",
            "database_migration": True,
        },
    )

    decision = shield.evaluate(request)

    print(f"outcome:    {decision.outcome}")
    print(f"allowed:    {decision.allowed}")
    print(f"confidence: {decision.confidence}")
    print(f"reason:     {decision.reason}")

    if decision.outcome == Outcome.DENY:
        print("\n-> Agent must not execute deploy.")
    elif decision.outcome == Outcome.REVIEW:
        print("\n-> Agent must obtain approval before executing.")
    else:
        print("\n-> Agent may proceed.")


if __name__ == "__main__":
    main()
