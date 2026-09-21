"""An ``AgentProvider`` backed by the Anthropic Claude API.

Requires the optional ``anthropic`` dependency: ``pip install -e ".[anthropic]"``
(the official ``anthropic`` package). Never imported by ``agentshield``'s
own ``__init__.py``, by ``agentshield.agent``, or by
``agentshield.providers``'s own ``__init__.py`` — importing ``agentshield``
never requires the ``anthropic`` package to be installed.

This module's only job is: user request -> proposed action. It never
executes anything, never calls AgentShield, and never makes a policy
decision. It is responsible for creating the Anthropic client, reading
``ANTHROPIC_API_KEY``, selecting the model, sending the request, and
converting the response into a provider-neutral ``ProposedAction`` — the
agent that uses this provider knows none of that.
"""

from __future__ import annotations

import json
import os
from typing import Optional

import anthropic

from ..agent import AgentProvider, ProposedAction, ProviderError

_DEFAULT_MODEL = "claude-haiku-4-5-20251001"
_MAX_TOKENS = 1024

_SYSTEM_PROMPT = (
    "You convert a user's request into a single structured action for an "
    "automation system to consider before executing it. Respond with ONLY "
    'a JSON object with exactly these keys: "action" (a short snake_case '
    'string identifying the action), "arguments" (a JSON object of '
    'action-specific data, e.g. a version string), and "context" (a JSON '
    "object describing the situation relevant to deciding whether the "
    "action should proceed, e.g. environment, timing, size of change). "
    "Do not include any other keys or any text outside the JSON object."
)

# Anthropic's native structured-output schema for the Messages API
# (output_config.format), rather than free-text JSON we'd have to hope
# for -- see https://platform.claude.com/docs/en/build-with-claude/structured-outputs
_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string"},
        "arguments": {"type": "object"},
        "context": {"type": "object"},
    },
    "required": ["action", "arguments", "context"],
}


class AnthropicProvider(AgentProvider):
    """Proposes an action via the Anthropic Messages API (structured output)."""

    def __init__(
        self, client: Optional[anthropic.Anthropic] = None, *, model: Optional[str] = None
    ) -> None:
        # anthropic.Anthropic() reads ANTHROPIC_API_KEY from the environment
        # by default; no key is ever hard-coded here.
        self._client = client if client is not None else anthropic.Anthropic()
        self._model = model or os.environ.get("ANTHROPIC_MODEL", _DEFAULT_MODEL)

    def propose_action(self, user_request: str) -> ProposedAction:
        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=_MAX_TOKENS,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_request}],
                output_config={
                    "format": {"type": "json_schema", "schema": _RESPONSE_SCHEMA}
                },
            )
        except Exception as exc:
            raise ProviderError(f"Anthropic request failed: {exc}") from exc

        try:
            content = response.content[0].text
            data = json.loads(content)
            return ProposedAction(
                action=data["action"],
                arguments=data.get("arguments", {}),
                context=data.get("context", {}),
            )
        except Exception as exc:
            raise ProviderError(
                f"Could not parse the Anthropic response as a proposed action: {exc}"
            ) from exc
