"""Minimal end-to-end demo: a simple Python agent consults AgentShield
before executing an action.

    Simple Python Agent
          |
          | proposes an action
          v
    AgentShield.evaluate()
          |
          | deterministic policy + optional semantic evaluation
          v
        Decision
          |
          v
    Agent either executes or stops

AgentShield is a decision layer here, not an execution proxy: it never
sits between the agent and whatever it would eventually call (a tool, an
MCP server, a deploy script, anything). The agent asks first, then
decides for itself whether to proceed — this demo's "agent" is just a
small Python function; no agent framework is involved.

**Policy knows the rules. Jev evaluates the situation.** Three scenarios
show the distinction:

  1. Deterministic DENY (production database deletion) — Jev is never
     even consulted, and could not override it if it were.
  2. Deterministic ALLOW (a staging deploy), but the situation — a large
     database migration, late on a Friday — is one Jev can flag for
     REVIEW even though policy alone would have allowed it.
  3. Deterministic ALLOW (a staging deploy), and Jev agrees it's routine.

Run:

    python examples/agent_demo.py

Deterministic policy works with no setup at all. Semantic evaluation via
Jev is used automatically when both the optional "jev" extra is installed
and TYPESAFE_API_KEY is set:

    pip install -e ".[jev]"
    export TYPESAFE_API_KEY=your_key_here   # never committed, never printed
    python examples/agent_demo.py

Otherwise the demo runs on deterministic policy alone and says so
explicitly in its output — it never fakes a semantic result.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Optional

from agentshield import Decision, DecisionEngine, DecisionRequest, Outcome, Policy, SemanticVerdict

# Some terminals (notably Windows consoles using a legacy codepage) can't
# encode the characters used below; fall back gracefully instead of crashing.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

# Two rules: an explicit DENY (deterministic policy is authoritative, full
# stop), and an explicit ALLOW (deterministic policy permits it, but that
# is not necessarily the end of the story — see semantic evaluation below).
POLICY = Policy.from_dict(
    {
        "rules": [
            {
                "name": "block-production-database-deletion",
                "tool": "delete_database",
                "context": {"environment": "production"},
                "outcome": "deny",
                "risk": "critical",
                "reason": "Production database deletion is never allowed.",
            },
            {
                "name": "allow-staging-deploys",
                "tool": "deploy",
                "context": {"environment": "staging"},
                "outcome": "allow",
                "risk": "low",
                "reason": "Staging deployments are permitted by policy.",
            },
        ]
    }
)


def _build_jev_shield() -> Optional[DecisionEngine]:
    """A DecisionEngine with real Jev semantic evaluation attached, or None.

    Never fakes a semantic evaluator: it is only returned when the real
    prerequisites (the optional "jev" extra installed, and
    TYPESAFE_API_KEY set) are met.
    """
    if not os.environ.get("TYPESAFE_API_KEY"):
        return None
    try:
        from agentshield.jev import JevSemanticEvaluator
    except ImportError:
        return None
    return DecisionEngine(POLICY, semantic_evaluator=JevSemanticEvaluator())


def _semantic_verdict_label(decision: Decision) -> Optional[str]:
    """Pull the plain GOOD/REVIEW/BAD label back out of decision.reason.

    DecisionEngine folds the semantic verdict into the existing `reason`
    string rather than adding a new Decision field (see engine.py) --
    this just un-folds it for a cleaner demo print. Returns None when no
    semantic assessment is present in the reason (e.g. a deterministic
    DENY, which Jev is never even consulted for).
    """
    reason_lower = decision.reason.lower()
    if "semantic assessment" not in reason_lower:
        return None
    for verdict in SemanticVerdict:
        if verdict.value in reason_lower:
            return verdict.value.upper()
    return None


def evaluate_scenario(
    action: str,
    context: dict[str, Any],
    arguments: dict[str, Any],
    jev_shield: Optional[DecisionEngine],
) -> tuple[Decision, Decision]:
    """Evaluate one scenario both without and with Jev.

    Returns (deterministic_only_decision, final_decision). `final_decision`
    equals `deterministic_only_decision` when `jev_shield` is None (Jev not
    configured) -- the deterministic result is never silently discarded,
    only ever added to.
    """
    request = DecisionRequest(action=action, actor="release-agent", context=context, arguments=arguments)

    deterministic_decision = DecisionEngine(POLICY).evaluate(request)

    if jev_shield is None:
        return deterministic_decision, deterministic_decision

    final_decision = jev_shield.evaluate(request)
    return deterministic_decision, final_decision


def run_scenario(
    number: int,
    title: str,
    action_label: str,
    action: str,
    context: dict[str, Any],
    arguments: dict[str, Any],
    jev_shield: Optional[DecisionEngine],
) -> tuple[Decision, bool]:
    """Print one scenario's evaluation and simulated execution. Returns
    (final_decision, executed).
    """
    print("═" * 39)
    print(f"Scenario {number}: {title}")
    print("═" * 39)
    print()
    print(f"Action:\n  {action_label}\n")
    print("Context:")
    for key, value in context.items():
        print(f"  {key}: {value}")
    print()

    deterministic_decision, final_decision = evaluate_scenario(action, context, arguments, jev_shield)

    print(f"Policy:\n  {deterministic_decision.reason} → {deterministic_decision.outcome.value.upper()}\n")

    print("Without Jev:")
    print(f"  {deterministic_decision.outcome.value.upper()}\n")

    print("With Jev:")
    if jev_shield is None:
        print("  not evaluated (TYPESAFE_API_KEY not set / 'jev' extra not installed)")
    else:
        verdict = _semantic_verdict_label(final_decision)
        if verdict is not None:
            print(f"  {verdict}")
            print(f"  Reason: {final_decision.reason}")
        else:
            # Deterministic DENY: Jev is never even consulted (see
            # engine.py) -- the outcome here is identical to "Without
            # Jev" above, not a coincidental agreement.
            print(f"  {final_decision.outcome.value.upper()} (Jev was not consulted -- deterministic DENY is final)")
    print()

    print(f"Final decision:\n  {final_decision.outcome.value.upper()}\n")

    executed = final_decision.outcome == Outcome.ALLOW
    if executed:
        print(f"\U0001F680 {action_label} executed.")
    elif final_decision.outcome == Outcome.REVIEW:
        print("⏸ Action not executed: requires human review.")
    else:
        print("\U0001F6D1 Action not executed: denied by policy.")
    print()

    return final_decision, executed


def main() -> None:
    jev_shield = _build_jev_shield()
    if jev_shield is None:
        print(
            "(Jev semantic evaluation is not configured for this run: set "
            "TYPESAFE_API_KEY and install the 'jev' extra to enable it. "
            "Showing deterministic-policy-only results below.)\n"
        )

    run_scenario(
        1,
        "Production database deletion",
        action_label="delete_database",
        action="delete_database",
        context={"environment": "production"},
        arguments={},
        jev_shield=jev_shield,
    )

    run_scenario(
        2,
        "Staging deployment, risky timing",
        action_label="deploy v2.4.1",
        action="deploy",
        context={
            "environment": "staging",
            "database_migration": True,
            "time": "friday_evening",
            "change_size": "large",
        },
        arguments={"version": "2.4.1"},
        jev_shield=jev_shield,
    )

    run_scenario(
        3,
        "Staging deployment, routine",
        action_label="deploy v2.4.1",
        action="deploy",
        context={
            "environment": "staging",
            "database_migration": False,
            "time": "tuesday_morning",
            "change_size": "small",
        },
        arguments={"version": "2.4.1"},
        jev_shield=jev_shield,
    )


if __name__ == "__main__":
    main()
