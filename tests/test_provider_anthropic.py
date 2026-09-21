"""Tests for agentshield.providers.anthropic.AnthropicProvider, with the
real anthropic client's network call mocked out.

These need the optional ``anthropic`` package importable (skipped cleanly
otherwise via ``pytest.importorskip``), but never touch the network or
require a real API key -- deterministic, offline unit tests.
"""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

anthropic = pytest.importorskip("anthropic", reason="requires the optional 'anthropic' extra")

from agentshield import ProposedAction, ProviderError
from agentshield.providers.anthropic import AnthropicProvider


def _mock_client(text: str) -> MagicMock:
    client = MagicMock()
    block = SimpleNamespace(text=text)
    client.messages.create.return_value = SimpleNamespace(content=[block])
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
    provider = AnthropicProvider(client=client)

    proposal = provider.propose_action("deploy v2.4.1 to staging")

    assert isinstance(proposal, ProposedAction)
    assert proposal.action == "deploy"
    assert proposal.arguments == {"version": "v2.4.1"}
    assert proposal.context == {"environment": "staging"}


def test_propose_action_sends_the_request_model_and_schema():
    client = _mock_client(json.dumps({"action": "deploy", "arguments": {}, "context": {}}))
    provider = AnthropicProvider(client=client, model="claude-test")

    provider.propose_action("deploy something")

    _, kwargs = client.messages.create.call_args
    assert kwargs["model"] == "claude-test"
    assert kwargs["messages"] == [{"role": "user", "content": "deploy something"}]
    assert kwargs["output_config"]["format"]["type"] == "json_schema"
    assert "action" in kwargs["output_config"]["format"]["schema"]["properties"]


def test_propose_action_defaults_missing_arguments_and_context():
    client = _mock_client(json.dumps({"action": "deploy"}))
    provider = AnthropicProvider(client=client)

    proposal = provider.propose_action("deploy")

    assert proposal.arguments == {}
    assert proposal.context == {}


# --- malformed provider response fails safely ----------------------------


def test_propose_action_raises_provider_error_on_invalid_json():
    client = _mock_client("this is not json")
    provider = AnthropicProvider(client=client)

    with pytest.raises(ProviderError):
        provider.propose_action("do something")


def test_propose_action_raises_provider_error_when_action_key_missing():
    client = _mock_client(json.dumps({"arguments": {}, "context": {}}))
    provider = AnthropicProvider(client=client)

    with pytest.raises(ProviderError):
        provider.propose_action("do something")


# --- API failure does NOT silently fall back -- it raises ---------------


def test_propose_action_raises_provider_error_when_the_api_call_fails():
    client = MagicMock()
    client.messages.create.side_effect = RuntimeError("connection reset")
    provider = AnthropicProvider(client=client)

    with pytest.raises(ProviderError) as exc_info:
        provider.propose_action("do something")
    assert "connection reset" in str(exc_info.value)


# --- construction / model selection --------------------------------------


def test_provider_defaults_to_a_real_anthropic_client_when_none_given():
    provider = AnthropicProvider()
    assert isinstance(provider._client, anthropic.Anthropic)


def test_provider_model_defaults_and_can_be_overridden(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    default_provider = AnthropicProvider(client=MagicMock())
    assert default_provider._model == "claude-haiku-4-5-20251001"

    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-env-model")
    env_provider = AnthropicProvider(client=MagicMock())
    assert env_provider._model == "claude-env-model"

    explicit_provider = AnthropicProvider(client=MagicMock(), model="claude-explicit")
    assert explicit_provider._model == "claude-explicit"
