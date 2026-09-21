"""End-to-end demo: an AI Agent proposes an action, explicitly asks
AgentShield to evaluate it, and only then executes it.

    LLM Provider
        |
        | proposes
        v
      Agent
        |
        | asks for a decision
        v
    AgentShield
        |-- Deterministic Policy
        +-- optional Jev semantic evaluation
        |
        v
    ALLOW / REVIEW / DENY
        |
        v
      Agent
        |
        | executes only after ALLOW
        v
      Action

AgentShield is NOT in the execution path here. It is a decision layer the
agent explicitly calls (`shield.evaluate(request)`) before doing anything;
the agent remains entirely responsible for executing (or not executing)
the proposed action. No side effects are ever performed for real;
`execute_action()` only simulates.

**The provider does not know about AgentShield. AgentShield does not know
about OpenAI. The agent connects the two.** Everything OpenAI-specific
lives in `agentshield.providers.openai.OpenAIProvider`; this script (the
agent) only ever talks to the provider-neutral `AgentProvider` interface
and `ProposedAction` from `agentshield.agent` -- it never imports the
`openai` package, never sees an API key, and never sees a provider
response object.

Run:

    python examples/real_agent_demo.py

Runs fully deterministically with no setup at all: without OPENAI_API_KEY,
`DeterministicDemoProvider` below (a small stand-in implementing the same
`AgentProvider` interface a real provider would) takes the LLM's place,
clearly labeled, never pretending to be a real LLM call; without
TYPESAFE_API_KEY, semantic evaluation is skipped (also clearly labeled).
Both can be enabled for real:

    pip install -e ".[agent-demo,jev]"
    export OPENAI_API_KEY=...      # never committed, never printed
    export TYPESAFE_API_KEY=...    # never committed, never printed
    python examples/real_agent_demo.py

If a real OpenAI provider is configured but its call fails, this demo
does NOT silently fall back to the deterministic stand-in -- it fails
safely and does not execute (see `run_agent` below).
"""

from __future__ import annotations

import os
import re
import sys
from typing import Any, Optional

from agentshield import (
    AgentProvider,
    Decision,
    DecisionEngine,
    Outcome,
    Policy,
    ProposedAction,
    ProviderError,
    SemanticVerdict,
    build_decision_request,
)

# Some terminals (notably Windows consoles using a legacy codepage) can't
# encode the characters used below; fall back gracefully instead of crashing.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

# Same shape as examples/agent_demo.py's policy: an explicit DENY and an
# explicit ALLOW, reused here rather than inventing a parallel policy.
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


# --- AgentShield setup (unaffected by which provider is used) -----------


def _build_shield() -> DecisionEngine:
    """A DecisionEngine, with real Jev semantic evaluation attached when
    TYPESAFE_API_KEY and the optional "jev" extra are both available.
    Never fakes a semantic evaluator.
    """
    if not os.environ.get("TYPESAFE_API_KEY"):
        return DecisionEngine(POLICY)
    try:
        from agentshield.jev import JevSemanticEvaluator
    except ImportError:
        return DecisionEngine(POLICY)
    return DecisionEngine(POLICY, semantic_evaluator=JevSemanticEvaluator())


# --- The deterministic fallback provider ---------------------------------
#
# Implements the exact same AgentProvider interface a real provider would,
# so the agent loop below never needs to know which one it's talking to.


class DeterministicDemoProvider(AgentProvider):
    """A small, deterministic stand-in for an LLM provider.

    Used only when OPENAI_API_KEY is not set. Does not attempt general
    natural-language understanding -- it is a simple, honest, clearly
    labeled substitute so the demo is runnable without an OpenAI API key,
    exactly like Jev's "not evaluated" fallback.
    """

    def propose_action(self, user_request: str) -> ProposedAction:
        text = user_request.lower()
        context: dict[str, Any] = {}

        if "production" in text:
            context["environment"] = "production"
        elif "staging" in text:
            context["environment"] = "staging"

        if "friday" in text:
            context["time"] = "friday_evening"
        elif "tuesday" in text:
            context["time"] = "tuesday_morning"

        if "migration" in text:
            context["database_migration"] = "no database migration" not in text

        if "large" in text:
            context["change_size"] = "large"
        elif "small" in text or "routine" in text:
            context["change_size"] = "small"

        arguments: dict[str, Any] = {}
        version_match = re.search(r"\bv?\d+\.\d+(?:\.\d+)?\b", user_request)
        if version_match:
            version = version_match.group(0)
            arguments["version"] = version if version.startswith("v") else f"v{version}"

        if "delete" in text and "database" in text:
            action = "delete_database"
        elif "deploy" in text:
            action = "deploy"
        else:
            action = "unknown_action"

        return ProposedAction(action=action, arguments=arguments, context=context)


def _build_provider() -> tuple[AgentProvider, bool]:
    """Returns (provider, used_real_llm).

    Provider selection (which provider to construct) is the only OpenAI-
    adjacent knowledge this demo script has: a presence check on
    OPENAI_API_KEY, nothing about the SDK itself. Once a provider is
    selected, it is never swapped -- see `run_agent` for what happens if
    a configured real provider then fails.
    """
    if not os.environ.get("OPENAI_API_KEY"):
        return DeterministicDemoProvider(), False
    try:
        from agentshield.providers.openai import OpenAIProvider
    except ImportError:
        return DeterministicDemoProvider(), False
    return OpenAIProvider(), True


def execute_action(action: str, arguments: dict[str, Any]) -> None:
    """Simulated execution only -- never a real side effect."""
    print(f"\U0001F680 Executed (simulated): {action} {arguments}")


# --- The agent loop: propose, ask AgentShield, then decide --------------


def run_agent(
    number: int,
    title: str,
    user_request: str,
    shield: DecisionEngine,
    provider: AgentProvider,
    used_real_llm: bool = False,
) -> tuple[Optional[Decision], bool]:
    """Propose an action for `user_request` via `provider`, consult
    AgentShield, and execute only if permitted. Returns (decision,
    executed); `decision` is None if the provider itself failed -- a
    provider failure is never a reason to fall back to a different
    provider or to execute anyway.
    """
    print("═" * 39)
    print(f"Scenario {number}: {title}")
    print("═" * 39)
    print()
    print(f'User request:\n  "{user_request}"\n')

    try:
        proposal = provider.propose_action(user_request)
    except ProviderError as exc:
        print(f"AI Agent could not propose an action: {exc}\n")
        print("\U0001F6D1 Not executed: the provider failed.")
        print()
        return None, False

    print("AI Agent proposes:")
    print(f"  action: {proposal.action}")
    print(f"  arguments: {proposal.arguments}")
    print(f"  context: {proposal.context}")
    if used_real_llm:
        print("  (via a real LLM call)")
    else:
        print("  (OPENAI_API_KEY not set -- using a deterministic stand-in, not a real LLM call)")
    print()

    request = build_decision_request(proposal)
    decision = shield.evaluate(request)

    print("AgentShield decision:")
    print(f"  {decision.outcome.value.upper()}")
    print(f"  Reason: {decision.reason}")
    print()

    executed = decision.outcome == Outcome.ALLOW
    if executed:
        execute_action(proposal.action, proposal.arguments)
    elif decision.outcome == Outcome.REVIEW:
        print("⏸ Not executed: requires human review.")
    else:
        print("\U0001F6D1 Not executed: denied by policy.")
    print()

    return decision, executed


def main() -> None:
    shield = _build_shield()
    if shield.semantic_evaluator is None:
        print(
            "(Jev semantic evaluation is not configured for this run: set "
            "TYPESAFE_API_KEY and install the 'jev' extra to enable it.)\n"
        )

    provider, used_real_llm = _build_provider()
    if not used_real_llm:
        print(
            "(OPENAI_API_KEY is not set: using a deterministic stand-in for "
            "the agent's action-proposal step instead of a real LLM call.)\n"
        )

    run_agent(
        1,
        "Production database deletion",
        "Delete the production database.",
        shield,
        provider,
        used_real_llm,
    )
    run_agent(
        2,
        "Risky staging deployment",
        "Deploy version v2.4.1 to staging. It's a large change including a "
        "database migration, and it's Friday evening.",
        shield,
        provider,
        used_real_llm,
    )
    run_agent(
        3,
        "Routine staging deployment",
        "Deploy version v2.4.1 to staging. It's a small, routine change on "
        "Tuesday morning, no database migration.",
        shield,
        provider,
        used_real_llm,
    )


if __name__ == "__main__":
    main()
