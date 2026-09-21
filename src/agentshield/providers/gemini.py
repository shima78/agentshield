"""An ``AgentProvider`` backed by the Google Gemini API.

Requires the optional ``gemini`` dependency: ``pip install -e ".[gemini]"``
(the official ``google-genai`` package). Never imported by ``agentshield``'s
own ``__init__.py``, by ``agentshield.agent``, or by
``agentshield.providers``'s own ``__init__.py`` — importing ``agentshield``
never requires the ``google-genai`` package to be installed.

This module's only job is: user request -> proposed action. It never
executes anything, never calls AgentShield, and never makes a policy
decision. It is responsible for creating the Gemini client, reading
``GEMINI_API_KEY``, selecting the model, sending the request, and
converting the response into a provider-neutral ``ProposedAction`` — the
agent that uses this provider knows none of that.
"""

from __future__ import annotations

import json
import os
from typing import Optional

from google import genai
from google.genai import types

from ..agent import AgentProvider, ProposedAction, ProviderError

_DEFAULT_MODEL = "gemini-flash-latest"

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

# Plain JSON Schema, passed via response_json_schema -- Gemini's
# alternative to response_schema for callers that already have a JSON
# Schema dict rather than a provider-specific schema object.
_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string"},
        "arguments": {"type": "object"},
        "context": {"type": "object"},
    },
    "required": ["action", "arguments", "context"],
}


class GeminiProvider(AgentProvider):
    """Proposes an action via the Google Gemini API (structured JSON output)."""

    def __init__(
        self, client: Optional[genai.Client] = None, *, model: Optional[str] = None
    ) -> None:
        # genai.Client() reads GEMINI_API_KEY (or GOOGLE_API_KEY) from the
        # environment by default; no key is ever hard-coded here.
        self._client = client if client is not None else genai.Client()
        self._model = model or os.environ.get("GEMINI_MODEL", _DEFAULT_MODEL)

    def propose_action(self, user_request: str) -> ProposedAction:
        try:
            response = self._client.models.generate_content(
                model=self._model,
                contents=user_request,
                config=types.GenerateContentConfig(
                    system_instruction=_SYSTEM_PROMPT,
                    response_mime_type="application/json",
                    response_json_schema=_RESPONSE_SCHEMA,
                ),
            )
        except Exception as exc:
            raise ProviderError(f"Gemini request failed: {exc}") from exc

        try:
            data = json.loads(response.text)
            return ProposedAction(
                action=data["action"],
                arguments=data.get("arguments", {}),
                context=data.get("context", {}),
            )
        except Exception as exc:
            raise ProviderError(
                f"Could not parse the Gemini response as a proposed action: {exc}"
            ) from exc
