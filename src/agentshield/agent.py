"""Provider-agnostic agent/action logic.

This module defines the interface an LLM (or any other) provider
implements to propose a structured action (``AgentProvider``,
``ProposedAction``), plus the one conversion step that adapts a proposal
onto the existing Core ``DecisionRequest`` API. It has no dependency on
any specific provider SDK — ``agentshield.providers.openai`` is one
implementation built on top of it, not the other way around, exactly like
``agentshield.jev`` is one implementation of ``agentshield.semantic``.

    LLM Provider
        |
        | proposes
        v
      Agent               (the caller's own loop; see examples/real_agent_demo.py)
        |
        | asks for a decision
        v
    AgentShield
        |
        | ALLOW / REVIEW / DENY
        v
      Agent
        |
        | executes only after ALLOW
        v
      Action

The provider does not know about AgentShield. AgentShield does not know
about the provider, or about any LLM at all. The agent connects the two —
this module only supplies the shared vocabulary (``ProposedAction``,
``AgentProvider``) and the small conversion step, not a full agent loop or
a multi-provider framework.
"""

from __future__ import annotations

import abc
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .policy import DecisionRequest


class ProviderError(Exception):
    """Raised when an ``AgentProvider`` cannot produce a ``ProposedAction``.

    Covers both a failed call to the underlying service and a malformed
    response that cannot be parsed into a valid proposal. Callers should
    treat this as "no action was proposed" and not execute anything — a
    provider failure is never a reason to silently substitute a different
    provider.
    """


class ProposedAction(BaseModel):
    """A provider-neutral, structured action proposal.

    This is the only thing an ``AgentProvider`` hands back to the agent;
    the agent never sees a provider-specific response object.
    """

    model_config = ConfigDict(frozen=True)

    action: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)


class AgentProvider(abc.ABC):
    """Proposes a structured action for a natural-language user request.

    A provider's only job is turning "what should I do?" into a
    ``ProposedAction``. It never executes anything, never calls
    AgentShield, and never makes a policy decision.
    """

    @abc.abstractmethod
    def propose_action(self, user_request: str) -> ProposedAction:
        """Return a ``ProposedAction`` for ``user_request``.

        Raises ``ProviderError`` if a proposal cannot be produced (the
        underlying call failed, or its response could not be parsed).
        """
        raise NotImplementedError


def build_decision_request(
    proposal: ProposedAction, *, actor: str = "ai-agent"
) -> DecisionRequest:
    """Adapt a ``ProposedAction`` directly onto the Core's ``DecisionRequest``
    API — no parallel abstraction.
    """
    return DecisionRequest(
        action=proposal.action,
        actor=actor,
        arguments=dict(proposal.arguments),
        context=dict(proposal.context),
    )
