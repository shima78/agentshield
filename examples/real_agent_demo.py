"""End-to-end demo: an AI Agent independently decides what action to take
for a natural-language user request, then asks AgentShield to evaluate
that proposed action before executing it.

    User
      |
      v
    AI Agent
      |
      | Agent decides what action to take
      v
    Proposed Action
      |
      v
    AgentShield
      |-- Deterministic Policy
      +-- optional Jev semantic judgment
      |
      v
    ALLOW / REVIEW / DENY
      |
      v
    AI Agent
      |
      | executes only if ALLOW
      v
    Action

**The Agent makes the decision about what to do. AgentShield evaluates
that proposed decision before execution.** AgentShield does not replace
the Agent's planning -- it is a decision boundary the Agent must pass
through before anything happens. Jev is not the agent and does not plan:
it is a semantic judge of the action the Agent already proposed.

This demo does NOT hard-code which action corresponds to which user
request. The three requests below are plain natural language; the Agent
(via a real LLM provider, or a clearly-labeled deterministic stand-in
when no LLM key is configured) independently derives the structured
action, arguments, and context from each one. AgentShield's policy/Jev
behavior then determines the outcome -- nothing here pre-announces what
that outcome will be.

**The provider does not know about AgentShield. AgentShield does not know
about OpenAI, Anthropic, or Gemini. The Agent connects the two.** Everything
provider-SDK-specific lives in `agentshield.providers.anthropic.AnthropicProvider`
/ `agentshield.providers.openai.OpenAIProvider` / `agentshield.providers.gemini.GeminiProvider`;
`OpenAIProvider` (and its siblings) is responsible only for asking the LLM
to produce the proposed action -- it never calls AgentShield, never
executes a tool, and never makes a policy decision. This script (the
Agent) only ever talks to the provider-neutral `AgentProvider` interface
and `ProposedAction` from `agentshield.agent` -- it never imports the
`anthropic`/`openai`/`google.genai` packages, never sees an API key, and
never sees a provider response object.

No real tools are ever called: `deploy`/`delete_database`/`search_repository`
below only print what they would have done. No MCP, no Docker, no real
external side effects.

Run:

    python examples/real_agent_demo.py

Runs fully deterministically with no setup at all: without
ANTHROPIC_API_KEY, OPENAI_API_KEY, or GEMINI_API_KEY,
`DeterministicDemoProvider` below (a small stand-in implementing the same
`AgentProvider` interface a real provider would) takes the LLM's place,
clearly labeled, never pretending to be a real LLM call; without
TYPESAFE_API_KEY, semantic evaluation is skipped (also clearly labeled).
Any can be enabled for real:

    pip install -e ".[agent-demo,jev]"
    export ANTHROPIC_API_KEY=...   # tried first; never committed, never printed
    export OPENAI_API_KEY=...      # tried next; never committed, never printed
    export GEMINI_API_KEY=...      # tried last; never committed, never printed
    export TYPESAFE_API_KEY=...    # never committed, never printed
    python examples/real_agent_demo.py

If a real provider is configured but its call fails, this demo does NOT
silently fall back to a different provider -- it fails safely and does
not execute (see `Agent.handle` below).
"""

from __future__ import annotations

import os
import re
import sys
from typing import Any, Callable, Optional

from agentshield import (
    AgentProvider,
    Decision,
    DecisionEngine,
    Outcome,
    Policy,
    ProposedAction,
    ProviderError,
    build_decision_request,
)

# Some terminals (notably Windows consoles using a legacy codepage) can't
# encode the characters used below; fall back gracefully instead of crashing.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

# Two rules: an explicit DENY (deterministic policy is authoritative, full
# stop) and an explicit ALLOW (permitted by policy -- but not necessarily
# the end of the story once Jev looks at the situation).
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
# so the Agent below never needs to know which one it's talking to. This
# is NOT the Agent being told the answer -- it independently derives the
# structured action from whatever facts are actually present in the
# request text, the same way a real LLM provider would, just without
# general language understanding.


class DeterministicDemoProvider(AgentProvider):
    """A small, deterministic stand-in for an LLM provider.

    Used only when no real provider key is set. Does not attempt general
    natural-language understanding -- it is a simple, honest, clearly
    labeled substitute so the demo is runnable without any LLM API key,
    exactly like Jev's "not evaluated" fallback. It derives the proposed
    action from facts actually present in the request text; it never
    invents context the request didn't state.
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
        elif "search" in text and "repositor" in text:
            action = "search_repository"
        else:
            action = "unknown_action"

        return ProposedAction(action=action, arguments=arguments, context=context)


def _build_provider() -> tuple[AgentProvider, bool]:
    """Returns (provider, used_real_llm).

    Provider selection (which provider to construct) is the only
    provider-adjacent knowledge this demo script has: a presence check on
    each provider's API key env var, nothing about any SDK itself.
    Anthropic is tried first, then OpenAI, then Gemini, then the
    deterministic fallback. Once a provider is selected, it is never
    swapped -- see `Agent.handle` for what happens if a configured real
    provider then fails.
    """
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            from agentshield.providers.anthropic import AnthropicProvider

            return AnthropicProvider(), True
        except ImportError:
            pass

    if os.environ.get("OPENAI_API_KEY"):
        try:
            from agentshield.providers.openai import OpenAIProvider

            return OpenAIProvider(), True
        except ImportError:
            pass

    if os.environ.get("GEMINI_API_KEY"):
        try:
            from agentshield.providers.gemini import GeminiProvider

            return GeminiProvider(), True
        except ImportError:
            pass

    return DeterministicDemoProvider(), False


# --- Simulated tools -------------------------------------------------------
#
# No MCP, no real side effects: each "tool" only prints what it would have
# done. The point of this demo is the decision boundary, not real
# execution.


def delete_database(**kwargs: Any) -> None:
    print(f"\U0001F5D1  Executed (simulated): delete_database {kwargs}")


def deploy(**kwargs: Any) -> None:
    print(f"\U0001F680 Executed (simulated): deploy {kwargs}")


def search_repository(**kwargs: Any) -> None:
    print(f"\U0001F50D Executed (simulated): search_repository {kwargs}")


def _unknown_action(action: str, **kwargs: Any) -> None:
    print(f"⚙️ Executed (simulated): {action} {kwargs}")


DEFAULT_TOOLS: dict[str, Callable[..., None]] = {
    "delete_database": delete_database,
    "deploy": deploy,
    "search_repository": search_repository,
}


# --- The Agent: connects a provider's proposal to AgentShield's decision --


class Agent:
    """Connects an LLM proposal to an AgentShield decision to execution.

    This class is the one place the critical invariant lives:

        provider -> ProposedAction -> AgentShield -> ALLOW -> execute

    never

        provider -> execute

    `_execute` is only ever called from inside the `Outcome.ALLOW` branch
    of `handle`; there is no other path to it. REVIEW and DENY never
    execute.
    """

    def __init__(
        self,
        provider: AgentProvider,
        shield: DecisionEngine,
        tools: Optional[dict[str, Callable[..., None]]] = None,
        actor: str = "ai-agent",
    ) -> None:
        self.provider = provider
        self.shield = shield
        self.tools = tools if tools is not None else dict(DEFAULT_TOOLS)
        self.actor = actor

    def handle(
        self,
        user_request: str,
        *,
        on_proposal: Optional[Callable[[ProposedAction], None]] = None,
        on_decision: Optional[Callable[[Decision], None]] = None,
    ) -> tuple[ProposedAction, Decision, bool]:
        """Propose an action for `user_request`, ask AgentShield, and
        execute only if the decision is ALLOW. Returns (proposal,
        decision, executed). Raises ProviderError if the provider itself
        fails -- the action is never executed in that case either, and
        this is never a reason to substitute a different provider.

        `on_proposal`/`on_decision` are optional observer hooks (used by
        `run_request` below for readable, chronologically-ordered output
        despite tools printing their own execution line); they cannot
        affect the ALLOW/REVIEW/DENY decision or whether execution happens.
        """
        proposal = self.provider.propose_action(user_request)
        if on_proposal is not None:
            on_proposal(proposal)

        request = build_decision_request(proposal, actor=self.actor)
        decision = self.shield.evaluate(request)
        if on_decision is not None:
            on_decision(decision)

        executed = False
        if decision.outcome == Outcome.ALLOW:
            self._execute(proposal)
            executed = True

        return proposal, decision, executed

    def _execute(self, proposal: ProposedAction) -> None:
        tool = self.tools.get(proposal.action)
        if tool is not None:
            tool(**proposal.arguments)
        else:
            _unknown_action(proposal.action, **proposal.arguments)


# --- Pretty-printing wrapper (kept separate from Agent's own logic) ------


def run_request(
    agent: Agent, user_request: str, used_real_llm: bool
) -> tuple[Optional[ProposedAction], Optional[Decision], bool]:
    print("-" * 60)
    print(f'User request: "{user_request}"')
    print("-" * 60)

    def on_proposal(proposal: ProposedAction) -> None:
        print("Agent proposes:")
        print(f"  action: {proposal.action}")
        print(f"  arguments: {proposal.arguments}")
        print(f"  context: {proposal.context}")
        via = "via a real LLM call" if used_real_llm else "deterministic stand-in, not a real LLM call"
        print(f"  ({via})")
        print()

    def on_decision(decision: Decision) -> None:
        print("AgentShield decision:")
        print(f"  {decision.outcome.value.upper()}")
        print(f"  Reason: {decision.reason}")
        # Printed before execution (if any) happens, so a tool's own
        # output -- if the decision is ALLOW -- reads as a consequence
        # of this decision, not the other way around.

    try:
        proposal, decision, executed = agent.handle(
            user_request, on_proposal=on_proposal, on_decision=on_decision
        )
    except ProviderError as exc:
        print(f"Agent could not propose an action: {exc}")
        print("Result: \U0001F6D1 not executed (the provider failed)\n")
        return None, None, False

    if executed:
        print("Result: ✅ executed")
    elif decision.outcome == Outcome.REVIEW:
        print("Result: ⏸ not executed (requires human review)")
    else:
        print("Result: \U0001F6D1 not executed (denied by policy)")
    print()

    return proposal, decision, executed


# These are plain natural-language requests. The Agent decides what
# structured action each one means -- nothing here states the answer.
USER_REQUESTS = [
    "Delete the production database.",
    "Deploy v2.4.1 to staging. It has a database migration, it's a large "
    "change, and we're doing it Friday evening.",
    "Deploy v2.4.1 to staging. It's a small change with no database "
    "migration and we're doing it Tuesday morning.",
]


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
            "(None of ANTHROPIC_API_KEY, OPENAI_API_KEY, or GEMINI_API_KEY "
            "is set: using a deterministic stand-in for the agent's "
            "action-proposal step instead of a real LLM call.)\n"
        )
    else:
        print(f"(Using {type(provider).__name__} for the agent's action-proposal step.)\n")

    agent = Agent(provider=provider, shield=shield)

    for user_request in USER_REQUESTS:
        run_request(agent, user_request, used_real_llm)


if __name__ == "__main__":
    main()
