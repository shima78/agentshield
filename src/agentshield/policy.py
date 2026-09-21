"""Policy and request models, plus deterministic rule matching.

This module defines:

* ``AuthorizationRequest`` — a typed description of an action being
  authorized.
* ``PolicyRule`` — a single, typed rule with optional matching constraints.
* ``Policy`` — an ordered collection of rules, loadable from YAML or dict.

Matching itself lives on ``PolicyRule.matches``; the *engine* (see
``engine.py``) is responsible for picking the highest-precedence rule among
the ones that match.
"""

from __future__ import annotations

import fnmatch
from typing import Any, Optional

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .decision import Outcome
from .risk import RiskLevel


class PolicyError(Exception):
    """Raised when a policy cannot be loaded or parsed."""


class AuthorizationRequest(BaseModel):
    """A typed description of an action an agent wants to perform."""

    model_config = ConfigDict(frozen=True)

    actor: str
    server: Optional[str] = None
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)


def _is_wildcard_pattern(pattern: str) -> bool:
    return "*" in pattern or "?" in pattern


def _tool_matches(pattern: str, tool: str) -> bool:
    if _is_wildcard_pattern(pattern):
        return fnmatch.fnmatchcase(tool, pattern)
    return pattern == tool


class PolicyRule(BaseModel):
    """A single policy rule.

    ``name``, ``outcome``, and ``risk`` are required on every rule. All
    matching fields (``actor``, ``server``, ``tool``, ``context``) are
    optional; an unset matching field never restricts matching — only
    fields the rule author explicitly sets are checked against the request.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    actor: Optional[str] = None
    server: Optional[str] = None
    tool: Optional[str] = None
    context: dict[str, Any] = Field(default_factory=dict)
    outcome: Outcome
    risk: RiskLevel
    reason: Optional[str] = None

    @field_validator("outcome", "risk", mode="before")
    @classmethod
    def _lowercase_string_enums(cls, value: Any) -> Any:
        if isinstance(value, str):
            return value.lower()
        return value

    def matches(self, request: AuthorizationRequest) -> bool:
        """Return True if this rule's constraints all hold for ``request``."""
        if self.actor is not None and self.actor != request.actor:
            return False
        if self.server is not None and self.server != request.server:
            return False
        if self.tool is not None and not _tool_matches(self.tool, request.tool):
            return False
        for key, value in self.context.items():
            if key not in request.context or request.context[key] != value:
                return False
        return True

    def is_exact_tool_match(self, request: AuthorizationRequest) -> bool:
        """True if ``tool`` is set and matches ``request.tool`` with no wildcard."""
        return (
            self.tool is not None
            and not _is_wildcard_pattern(self.tool)
            and self.tool == request.tool
        )

    def is_wildcard_tool_match(self, request: AuthorizationRequest) -> bool:
        """True if ``tool`` is a wildcard pattern that matches ``request.tool``."""
        return (
            self.tool is not None
            and _is_wildcard_pattern(self.tool)
            and _tool_matches(self.tool, request.tool)
        )


class Policy(BaseModel):
    """An ordered collection of policy rules."""

    model_config = ConfigDict(frozen=True)

    rules: list[PolicyRule] = Field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Policy":
        """Build a ``Policy`` from a plain dict (e.g. parsed YAML/JSON)."""
        return cls.model_validate(data)

    @classmethod
    def from_yaml(cls, path: str) -> "Policy":
        """Load a ``Policy`` from a YAML file on disk."""
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = yaml.safe_load(handle)
        except yaml.YAMLError as exc:
            raise PolicyError(f"Failed to parse policy YAML at {path!r}: {exc}") from exc

        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise PolicyError(
                f"Policy file {path!r} must contain a mapping with a 'rules' key, "
                f"got {type(data).__name__}."
            )
        return cls.from_dict(data)
