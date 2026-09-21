"""Minimal SDK-style usage: AgentShield as a decision engine, no MCP at all.

This is the primary intended way to use AgentShield: an agent or
application consults it before taking an action, then decides for itself
what to do with the answer.

    AgentShield decides. The agent/application executes.

AgentShield does not execute anything here, and does not need to sit
between the agent and whatever it eventually calls (an MCP server, a REST
API, a shell command, anything) — it only needs to be consulted first.

Run:

    python examples/sdk_example.py

No network access, no external services, no MCP dependency required.
"""

from __future__ import annotations

import pathlib

from agentshield import DecisionEngine, DecisionRequest, Outcome, Policy

# Reuses the same real-world policy from the GitHub MCP integration example
# (examples/github/policy.yaml) to show that the decision engine is the
# same regardless of whether MCP is involved at all.
POLICY_PATH = pathlib.Path(__file__).parent / "github" / "policy.yaml"


def main() -> None:
    policy = Policy.from_yaml(str(POLICY_PATH))
    shield = DecisionEngine(policy)

    decision = shield.evaluate(
        DecisionRequest(
            action="delete_repository",
            actor="agent",
            server="github",
            context={"environment": "production"},
        )
    )

    print(f"outcome:    {decision.outcome}")
    print(f"allowed:    {decision.allowed}")
    print(f"risk:       {decision.risk}")
    print(f"reason:     {decision.reason}")
    print(f"rule:       {decision.rule}")

    if decision.outcome == Outcome.DENY:
        # The agent must not execute the action.
        print("\n-> Agent must not execute delete_repository.")
    elif decision.outcome == Outcome.REVIEW:
        # The agent must route this through its own approval flow before
        # executing — AgentShield does not perform that flow itself.
        print("\n-> Agent must obtain approval before executing.")
    else:
        # The agent may proceed to actually perform the action, however
        # it chooses to (MCP, a direct API call, a CLI, ...).
        print("\n-> Agent may proceed.")


if __name__ == "__main__":
    main()
