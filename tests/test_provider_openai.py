"""Tests for agentshield.providers.openai.OpenAIProvider, with the real
openai client's network call mocked out.

These need the optional ``openai`` package importable (skipped cleanly
otherwise via ``pytest.importorskip``), but never touch the network or
require a real API key -- deterministic, offline unit tests.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

openai = pytest.importorskip("openai", reason="requires the optional 'agent-demo' extra")

from agentshield import ProposedAction, ProviderError
from agentshield.providers.openai import OpenAIProvider


def _mock_client(content: str) -> MagicMock:
    client = MagicMock()
    message = SimpleNamespace(content=content)
    choice = SimpleNamespace(message=message)
    client.chat.completions.create.return_value = SimpleNamespace(choices=[choice])
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
    provider = OpenAIProvider(client=client)

    proposal = provider.propose_action("deploy v2.4.1 to staging")

    assert isinstance(proposal, ProposedAction)
    assert proposal.action == "deploy"
    assert proposal.arguments == {"version": "v2.4.1"}
    assert proposal.context == {"environment": "staging"}


def test_propose_action_sends_the_request_and_model():
    client = _mock_client(json.dumps({"action": "deploy", "arguments": {}, "context": {}}))
    provider = OpenAIProvider(client=client, model="gpt-test")

    provider.propose_action("deploy something")

    _, kwargs = client.chat.completions.create.call_args
    assert kwargs["model"] == "gpt-test"
    assert kwargs["response_format"] == {"type": "json_object"}
    assert kwargs["messages"][-1] == {"role": "user", "content": "deploy something"}


def test_propose_action_defaults_missing_arguments_and_context():
    client = _mock_client(json.dumps({"action": "deploy"}))
    provider = OpenAIProvider(client=client)

    proposal = provider.propose_action("deploy")

    assert proposal.arguments == {}
    assert proposal.context == {}


# --- malformed provider response fails safely ----------------------------


def test_propose_action_raises_provider_error_on_invalid_json():
    client = _mock_client("this is not json")
    provider = OpenAIProvider(client=client)

    with pytest.raises(ProviderError):
        provider.propose_action("do something")


def test_propose_action_raises_provider_error_when_action_key_missing():
    client = _mock_client(json.dumps({"arguments": {}, "context": {}}))
    provider = OpenAIProvider(client=client)

    with pytest.raises(ProviderError):
        provider.propose_action("do something")


# --- API failure does NOT silently fall back -- it raises ---------------


def test_propose_action_raises_provider_error_when_the_api_call_fails():
    client = MagicMock()
    client.chat.completions.create.side_effect = RuntimeError("connection reset")
    provider = OpenAIProvider(client=client)

    with pytest.raises(ProviderError) as exc_info:
        provider.propose_action("do something")
    assert "connection reset" in str(exc_info.value)


# --- construction reads OPENAI_API_KEY from the environment --------------


def test_provider_construction_fails_clearly_without_an_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(Exception):
        OpenAIProvider()


def test_provider_construction_succeeds_with_an_api_key_present(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key-for-construction-only")
    provider = OpenAIProvider()
    assert isinstance(provider._client, openai.OpenAI)
