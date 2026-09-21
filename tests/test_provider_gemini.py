"""Tests for agentshield.providers.gemini.GeminiProvider, with the real
google-genai client's network call mocked out.

These need the optional ``google-genai`` package importable (skipped
cleanly otherwise via ``pytest.importorskip``), but never touch the
network or require a real API key -- deterministic, offline unit tests.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

genai = pytest.importorskip("google.genai", reason="requires the optional 'gemini' extra")

from agentshield import ProposedAction, ProviderError
from agentshield.providers.gemini import GeminiProvider


def _mock_client(text: str) -> MagicMock:
    client = MagicMock()
    client.models.generate_content.return_value = SimpleNamespace(text=text)
    return client


# --- converts a mocked structured response into ProposedAction ----------


def test_propose_action_parses_a_valid_json_response():
    client = _mock_client(
        json.dumps(
            {
                "action": "deploy",
                "arguments": {"version": "v2.4.1"},
                "context": {"environment": "staging"},
            }
        )
    )
    provider = GeminiProvider(client=client)

    proposal = provider.propose_action("deploy v2.4.1 to staging")

    assert isinstance(proposal, ProposedAction)
    assert proposal.action == "deploy"
    assert proposal.arguments == {"version": "v2.4.1"}
    assert proposal.context == {"environment": "staging"}


def test_propose_action_sends_the_request_model_and_schema():
    client = _mock_client(json.dumps({"action": "deploy", "arguments": {}, "context": {}}))
    provider = GeminiProvider(client=client, model="gemini-test")

    provider.propose_action("deploy something")

    _, kwargs = client.models.generate_content.call_args
    assert kwargs["model"] == "gemini-test"
    assert kwargs["contents"] == "deploy something"
    assert kwargs["config"].response_mime_type == "application/json"
    assert "action" in kwargs["config"].response_json_schema["properties"]


def test_propose_action_defaults_missing_arguments_and_context():
    client = _mock_client(json.dumps({"action": "deploy"}))
    provider = GeminiProvider(client=client)

    proposal = provider.propose_action("deploy")

    assert proposal.arguments == {}
    assert proposal.context == {}


# --- malformed provider response fails safely ----------------------------


def test_propose_action_raises_provider_error_on_invalid_json():
    client = _mock_client("this is not json")
    provider = GeminiProvider(client=client)

    with pytest.raises(ProviderError):
        provider.propose_action("do something")


def test_propose_action_raises_provider_error_when_action_key_missing():
    client = _mock_client(json.dumps({"arguments": {}, "context": {}}))
    provider = GeminiProvider(client=client)

    with pytest.raises(ProviderError):
        provider.propose_action("do something")


# --- API failure does NOT silently fall back -- it raises ---------------


def test_propose_action_raises_provider_error_when_the_api_call_fails():
    client = MagicMock()
    client.models.generate_content.side_effect = RuntimeError("connection reset")
    provider = GeminiProvider(client=client)

    with pytest.raises(ProviderError) as exc_info:
        provider.propose_action("do something")
    assert "connection reset" in str(exc_info.value)


# --- construction reads GEMINI_API_KEY from the environment --------------


def test_provider_construction_fails_clearly_without_an_api_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(Exception):
        GeminiProvider()


def test_provider_model_defaults_and_can_be_overridden(monkeypatch):
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    default_provider = GeminiProvider(client=MagicMock())
    assert default_provider._model == "gemini-flash-latest"

    monkeypatch.setenv("GEMINI_MODEL", "gemini-env-model")
    env_provider = GeminiProvider(client=MagicMock())
    assert env_provider._model == "gemini-env-model"

    explicit_provider = GeminiProvider(client=MagicMock(), model="gemini-explicit")
    assert explicit_provider._model == "gemini-explicit"
