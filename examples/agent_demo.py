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

Run:

    python examples/agent_demo.py

Deterministic policy works with no setup at all. Semantic evaluation via
Jev is used automatically when both the optional "jev" extra is installed
and TYPESAFE_API_KEY is set:

    pip install -e ".[jev]"
    export TYPESAFE_API_KEY=your_key_here   # never committed
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
# encode the emoji used below; fall back gracefully instead of crashing.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

# A small policy describing the one rule relevant to this demo: production
# deployments require human review. (Deterministic policy defaults to
# ALLOW for anything it has no opinion on — see the Core README section.)
POLICY = Policy.from_dict(
    {
        "rules": [
            {
                "name": "production-deploy-review",
                "tool": "deploy",
                "context": {"environment": "production"},
                "outcome": "review",
                "risk": "high",
                "reason": "Production deployments require human approval.",
            },
        ]
    }
)


def _build_shield() -> tuple[DecisionEngine, bool]:
    """Build the DecisionEngine, adding Jev semantic evaluation when available.

    Returns (engine, semantic_evaluation_enabled). Never fakes a semantic
    evaluator: it is only attached when the real prerequisites (the
    optional "jev" extra installed, and TYPESAFE_API_KEY set) are met.
    """
    if not os.environ.get("TYPESAFE_API_KEY"):
        return DecisionEngine(POLICY), False

    try:
        from agentshield.jev import JevSemanticEvaluator
    except ImportError:
        return DecisionEngine(POLICY), False

    return DecisionEngine(POLICY, semantic_evaluator=JevSemanticEvaluator()), True


def _build_request(version: str, context: dict[str, Any]) -> DecisionRequest:
    return DecisionRequest(
        action="deploy",
        actor="release-agent",
        context=context,
        arguments={"version": version},
    )


def _semantic_verdict_label(decision: Decision) -> Optional[str]:
    """Pull the plain GOOD/REVIEW/BAD label back out of decision.reason.

    DecisionEngine folds the semantic verdict into the existing `reason`
    string rather than adding a new Decision field (see engine.py) --
    this just un-folds it for a cleaner demo print.
    """
    reason_lower = decision.reason.lower()
    for verdict in SemanticVerdict:
        if f"semantic assessment" in reason_lower and verdict.value in reason_lower:
            return verdict.value.upper()
    return None


def propose_deploy(
    shield: DecisionEngine, version: str, context: dict[str, Any]
) -> tuple[Decision, bool]:
    """The "agent": proposes one action, consults AgentShield, then executes
    only if permitted. Returns (decision, executed) for callers/tests.
    """
    print(f"Agent wants to:\n  deploy version {version}\n")
    print("Context:")
    for key, value in context.items():
        print(f"  {key}: {value}")
    print()

    request = _build_request(version, context)
    decision = shield.evaluate(request)

    print("AgentShield")
    print(f"  Policy: {'matched (' + decision.rule + ')' if decision.rule else 'no rule matched (default allow)'}")
    if shield.semantic_evaluator is not None:
        verdict = _semantic_verdict_label(decision)
        print("  Semantic evaluation: Jev")
        print(f"  Assessment: {verdict if verdict else 'unavailable'}")
    else:
        print("  Semantic evaluation: unavailable (no TYPESAFE_API_KEY / jev extra) -- deterministic policy only")
    print(f"  Reason: {decision.reason}")
    print()

    print(f"Decision: {decision.outcome.value.upper()}")
    print()

    executed = decision.outcome == Outcome.ALLOW
    if executed:
        print(f"\U0001F680 Deploying version {version}...")
    elif decision.outcome == Outcome.REVIEW:
        print("⏸ Deployment not executed: requires human review.")
    else:
        print("\U0001F6D1 Deployment not executed: denied by policy.")

    return decision, executed


def main() -> None:
    shield, jev_enabled = _build_shield()
    if not jev_enabled:
        print(
            "(Semantic evaluation via Jev is not configured for this run: "
            "set TYPESAFE_API_KEY and install the 'jev' extra to enable it.)\n"
        )

    propose_deploy(
        shield,
        version="2.4.1",
        context={
            "environment": "production",
            "database_migration": True,
            "time": "friday_evening",
        },
    )


if __name__ == "__main__":
    main()
