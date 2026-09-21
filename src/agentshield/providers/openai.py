"""An ``AgentProvider`` backed by the OpenAI API (or an OpenAI-compatible one).

Requires the optional ``agent-demo`` dependency: ``pip install -e ".[agent-demo]"``
(the official ``openai`` package). Never imported by ``agentshield``'s own
``__init__.py``, by ``agentshield.agent``, or by ``agentshield.providers``'s
own ``__init__.py`` — importing ``agentshield`` never requires the
``openai`` package to be installed.

This module's only job is: user request -> proposed action. It never
executes anything, never calls AgentShield, and never makes a policy
decision. It is responsible for creating the OpenAI client, reading
``OPENAI_API_KEY``, selecting the model, sending the request, and
converting the response into a provider-neutral ``ProposedAction`` — the
agent that uses this provider knows none of that.
"""

from __future__ import annotations

import json
import os
from typing import Optional

import openai

from ..agent import AgentProvider, ProposedAction, ProviderError

_DEFAULT_MODEL = "gpt-4o-mini"

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


class OpenAIProvider(AgentProvider):
    """Proposes an action via the OpenAI chat completions API (JSON mode)."""

    def __init__(
        self, client: Optional[openai.OpenAI] = None, *, model: Optional[str] = None
    ) -> None:
        # openai.OpenAI() reads OPENAI_API_KEY from the environment by
        # default; no key is ever hard-coded here.
        self._client = client if client is not None else openai.OpenAI()
        self._model = model or os.environ.get("OPENAI_MODEL", _DEFAULT_MODEL)

    def propose_action(self, user_request: str) -> ProposedAction:
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": user_request},
                ],
                response_format={"type": "json_object"},
            )
        except Exception as exc:
            raise ProviderError(f"OpenAI request failed: {exc}") from exc

        try:
            content = response.choices[0].message.content
            data = json.loads(content)
            return ProposedAction(
                action=data["action"],
                arguments=data.get("arguments", {}),
                context=data.get("context", {}),
            )
        except Exception as exc:
            raise ProviderError(
                f"Could not parse the OpenAI response as a proposed action: {exc}"
            ) from exc
