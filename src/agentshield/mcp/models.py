"""Configuration models for the MCP gateway.

These describe *how to run a gateway* (which downstream server to launch,
which policy file to load, what static context to attach) — they are
separate from, and do not modify, the Core's ``Policy``/``PolicyRule``
models.
"""

from __future__ import annotations

from typing import Any, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field

from .errors import GatewayConfigError


class ServerConfig(BaseModel):
    """Identifies the downstream MCP server for policy purposes.

    ``name`` is used verbatim as ``DecisionRequest.server``, so policy
    rules can match on it (e.g. ``server: github``).
    """

    model_config = ConfigDict(frozen=True)

    name: str


class DownstreamConfig(BaseModel):
    """How to launch the downstream MCP server as a local subprocess over stdio."""

    model_config = ConfigDict(frozen=True)

    command: str
    args: list[str] = Field(default_factory=list)
    env: Optional[dict[str, str]] = None


class PolicyConfig(BaseModel):
    """Where to load the policy that governs this gateway."""

    model_config = ConfigDict(frozen=True)

    path: str


class GatewayConfig(BaseModel):
    """Top-level configuration for an ``MCPGateway``.

    Example:

        server:
          name: github
        downstream:
          command: python
          args: [server.py]
        context:
          environment: production
        policy:
          path: examples/policy.yaml
    """

    model_config = ConfigDict(frozen=True)

    server: ServerConfig
    downstream: DownstreamConfig
    policy: PolicyConfig
    actor: str = "agent"
    context: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GatewayConfig":
        return cls.model_validate(data)

    @classmethod
    def from_yaml(cls, path: str) -> "GatewayConfig":
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = yaml.safe_load(handle)
        except yaml.YAMLError as exc:
            raise GatewayConfigError(
                f"Failed to parse gateway config YAML at {path!r}: {exc}"
            ) from exc

        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise GatewayConfigError(
                f"Gateway config file {path!r} must contain a mapping, "
                f"got {type(data).__name__}."
            )
        return cls.from_dict(data)
