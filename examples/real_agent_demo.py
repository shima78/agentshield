"""End-to-end demo: an AI Agent proposes an action, explicitly asks
AgentShield to evaluate it, and only then executes it.

    User
      |
      v
    AI Agent
      |
      | proposed action
      v
    AgentShield
      |-- Deterministic Policy
      +-- optional Jev semantic evaluation
      |
      v
    ALLOW / REVIEW / DENY
      |
      v
    AI Agent
      |
      v
    Execute Action

AgentShield is NOT in the execution path here. It is a decision layer the
agent explicitly calls (`shield.evaluate(request)`) before doing anything;
the agent remains entirely responsible for executing (or not executing)
the proposed action. The LLM's only job is to propose an action from a
user's natural-language request -- AgentShield's job is deciding whether
that proposed action should proceed. No side effects are ever performed
for real; `execute_action()` only simulates.

Run:

    python examples/real_agent_demo.py

Runs fully deterministically with no setup at all: without OPENAI_API_KEY,
a small deterministic stand-in takes the LLM's place for the "propose an
action" step (clearly labeled, never pretending to be a real LLM call);
without TYPESAFE_API_KEY, semantic evaluation is skipped (also clearly
labeled). Both can be enabled for real:

    pip install -e ".[agent-demo,jev]"
    export OPENAI_API_KEY=...      # never committed, never printed
    export TYPESAFE_API_KEY=...    # never committed, never printed
    python examples/real_agent_demo.py
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Any, Optional

from agentshield import Decision, DecisionEngine, DecisionRequest, Outcome, Policy, SemanticVerdict

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


# --- AgentShield setup (unaffected by whether an LLM is configured) -----


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


# --- The LLM boundary: propose a structured action, nothing more --------
#
# LLM: "What action should I take?"
# AgentShield: "Should this proposed action proceed?"
#
# The LLM (real or the deterministic stand-in below) only ever produces a
# plain {"action", "arguments", "context"} dict -- it never decides
# whether to execute. AgentShield's decision is the only thing that does.

_PROPOSAL_SYSTEM_PROMPT = (
    "You convert a user's request into a single structured action for an "
    "automation system to consider before executing it. Respond with ONLY "
    'a JSON object with exactly these keys: "action" (a short snake_case '
    'string identifying the action), "arguments" (a JSON object of '
    'action-specific data, e.g. a version string), and "context" (a JSON '
    "object describing the situation relevant to deciding whether the "
    "action should proceed, e.g. environment, timing, size of change). "
    "Do not include any other keys or any text outside the JSON object."
)


def _build_openai_client() -> Optional[Any]:
    if not os.environ.get("OPENAI_API_KEY"):
        return None
    try:
        import openai
    except ImportError:
        return None
    return openai.OpenAI()


def _fallback_propose_action(user_request: str) -> dict[str, Any]:
    """A small, deterministic stand-in for the LLM's proposal step.

    Used only when no real LLM is configured. This does not attempt
    general natural-language understanding -- it is a simple, honest,
    clearly-labeled substitute so the demo is runnable without an
    OpenAI API key, exactly like Jev's "not evaluated" fallback.
    """
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

    return {"action": action, "arguments": arguments, "context": context}


def propose_action_with_llm(
    user_request: str, client: Optional[Any] = None
) -> tuple[dict[str, Any], bool]:
    """Turn a natural-language request into a structured action.

    Returns (proposed_action, used_real_llm). Uses `client` if given
    (mainly for tests); otherwise builds one from OPENAI_API_KEY, falling
    back to a deterministic stand-in when no key/client is available.
    """
    if client is None:
        client = _build_openai_client()

    if client is None:
        return _fallback_propose_action(user_request), False

    response = client.chat.completions.create(
        model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": _PROPOSAL_SYSTEM_PROMPT},
            {"role": "user", "content": user_request},
        ],
        response_format={"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content), True


def build_decision_request(proposed: dict[str, Any]) -> DecisionRequest:
    """Adapt a proposed action straight onto the existing DecisionRequest
    API -- no parallel abstraction.
    """
    return DecisionRequest(
        action=proposed["action"],
        actor="ai-agent",
        arguments=proposed.get("arguments", {}),
        context=proposed.get("context", {}),
    )


def execute_action(action: str, arguments: dict[str, Any]) -> None:
    """Simulated execution only -- never a real side effect."""
    print(f"\U0001F680 Executed (simulated): {action} {arguments}")


# --- The agent loop: propose, ask AgentShield, then decide --------------


def run_agent(
    number: int,
    title: str,
    user_request: str,
    shield: DecisionEngine,
    llm_client: Optional[Any] = None,
) -> tuple[Decision, bool]:
    """Propose an action for `user_request`, consult AgentShield, and
    execute only if permitted. Returns (decision, executed).
    """
    print("═" * 39)
    print(f"Scenario {number}: {title}")
    print("═" * 39)
    print()
    print(f'User request:\n  "{user_request}"\n')

    proposed, used_llm = propose_action_with_llm(user_request, client=llm_client)
    print("AI Agent proposes:")
    print(f"  action: {proposed.get('action')}")
    print(f"  arguments: {proposed.get('arguments', {})}")
    print(f"  context: {proposed.get('context', {})}")
    if used_llm:
        print("  (via a real LLM call)")
    else:
        print("  (OPENAI_API_KEY not set -- using a deterministic stand-in, not a real LLM call)")
    print()

    request = build_decision_request(proposed)
    decision = shield.evaluate(request)

    print("AgentShield decision:")
    print(f"  {decision.outcome.value.upper()}")
    print(f"  Reason: {decision.reason}")
    print()

    executed = decision.outcome == Outcome.ALLOW
    if executed:
        execute_action(proposed.get("action", ""), proposed.get("arguments", {}))
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

    llm_client = _build_openai_client()
    if llm_client is None:
        print(
            "(OPENAI_API_KEY is not set: using a deterministic stand-in for "
            "the agent's action-proposal step instead of a real LLM call.)\n"
        )

    run_agent(
        1,
        "Production database deletion",
        "Delete the production database.",
        shield,
        llm_client,
    )
    run_agent(
        2,
        "Risky staging deployment",
        "Deploy version v2.4.1 to staging. It's a large change including a "
        "database migration, and it's Friday evening.",
        shield,
        llm_client,
    )
    run_agent(
        3,
        "Routine staging deployment",
        "Deploy version v2.4.1 to staging. It's a small, routine change on "
        "Tuesday morning, no database migration.",
        shield,
        llm_client,
    )


if __name__ == "__main__":
    main()
