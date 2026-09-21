# AgentShield

AgentShield is a policy and authorization layer for AI agents and AI-native
applications. It decides whether an action an agent wants to take should be
**allowed**, sent for **review**, or **denied** — deterministically, based on
rules you define, before the action is executed.

## Why it exists

AI agents increasingly call tools that have real-world side effects: merging
pull requests, deleting resources, reading secrets, moving money. Provider
SDKs and MCP servers give you the *mechanism* to call these tools, but not a
consistent, auditable way to decide *whether a given call should be allowed*.
AgentShield is that decision layer — independent of any specific LLM
provider, agent framework, or tool protocol.

## Current architecture

This repository currently implements **Phase 1: the Core only**.

```text
                    AgentShield
                         │
                  ┌──────┴──────┐
                  │     Core     │   ← implemented here
                  │              │
                  │ Policy       │
                  │ Decision     │
                  │ Risk         │
                  │ Audit        │
                  │ Providers    │
                  └──────┬───────┘
                         │
              ┌──────────┴──────────┐
              ▼                     ▼
         Python SDK            MCP Gateway      ← not yet implemented
              │                     │
         Developers          AI Agents / MCP
```

The Core is a small, dependency-light Python library. It is deterministic: it
never calls an external service, never calls an LLM, and never executes the
action it is authorizing. Given a policy and a request, it always returns the
same decision.

## Minimal example

```python
from agentshield import AuthorizationEngine, AuthorizationRequest, Policy

policy = Policy.from_yaml("examples/policy.yaml")
engine = AuthorizationEngine(policy)

request = AuthorizationRequest(
    actor="agent",
    server="github",
    tool="merge_pull_request",
    arguments={"repo": "acme/app", "pull_request": 42},
    context={"environment": "production"},
)

decision = engine.evaluate(request)

print(decision.outcome)   # Outcome.REVIEW
print(decision.allowed)   # False
print(decision.reason)    # "Production merges require human approval."
print(decision.rule)      # "production-merge"
```

## Example policy

```yaml
rules:
  - name: block-secret-access
    tool: "*.read_secret"
    outcome: deny
    risk: critical
    reason: "Access to secrets is blocked."

  - name: production-merge
    server: github
    tool: merge_pull_request
    context:
      environment: production
    outcome: review
    risk: high
    reason: "Production merges require human approval."
```

Rules support `actor`, `server`, `tool`, and `context` as optional matching
constraints (all must be unset or match for a rule to apply). `name`,
`outcome`, and `risk` are required on every rule; `reason` is optional. Tool
names support `*`/`?` wildcards (e.g. `"*.delete_*"`); `actor` and `server`
currently require exact matches.

See [`examples/policy.yaml`](examples/policy.yaml) for a fuller example, and
`tests/test_engine.py` for the precedence rules worked out in detail.

## ALLOW / REVIEW / DENY semantics

| Outcome | `allowed` | Meaning |
|---|---|---|
| `ALLOW`  | `True`  | The action may proceed. |
| `REVIEW` | `False` | The action must not proceed automatically; it needs human (or other) approval. |
| `DENY`   | `False` | The action must not proceed, period. |

If no rule in the policy matches a request, the Core defaults to `ALLOW`
with `risk = LOW` and `confidence = 1.0`. This default is intentionally easy
to change in a future phase (e.g. a "default deny" mode) but is not
configurable yet — see "Open questions" below.

### Rule precedence

When multiple rules match a request, the Core picks exactly one, using this
deterministic order (implemented in `engine.py`):

1. **Exact tool match** beats **wildcard tool match** beats **no tool
   constraint**.
2. Within the same tier, the rule with **more context constraints** wins.
3. Within a further tie, the rule with **more specific `actor`/`server`
   constraints** wins.
4. Within a full tie, the **earlier rule** in the policy file wins.

This ordering depends only on each rule's own fields and its position in the
policy — never on dictionary iteration order — so the same policy and
request always produce the same decision.

### Deterministic DENY is authoritative

**A deterministic `DENY` from the Core must never be overridden by another
layer.** Future providers (for example, an LLM-based reasoning layer such as
"Jev") may add context, explanations, or additional review — but they cannot
flip a policy-level `DENY` into an `ALLOW`. This is a Core design invariant,
not just a convention: the engine's precedence rules operate purely over
policy rules, and nothing in this codebase exposes a way for an external
caller to override a returned `Decision`. Any future integration that wants
to add a "second opinion" must be additive (e.g. escalating `REVIEW` to
`DENY`), never permissive.

## What Phase 1 is (and isn't)

Phase 1 implements **only the Core**: typed decision/request/policy models,
deterministic matching and precedence, a default-allow fallback, and an
in-memory audit log. It deliberately does **not** implement a Python SDK, an
MCP gateway, a CLI, an HTTP server, a database, or any integration with an
LLM provider (Anthropic, OpenAI, etc.) or specific tool ecosystem (GitHub,
ComfyUI, ...). Those are later phases, built on top of this Core.

## Roadmap

```text
Core (this repo, Phase 1)
→ Python SDK
→ MCP Gateway
→ Jev provider
→ TypeScript SDK
→ integrations
```

## Installing and running tests

```bash
pip install -e ".[dev]"
pytest
```

## Status

AgentShield is an early-stage, open-source project. It does not make any
production security guarantees; treat it as policy infrastructure you
integrate and test against your own threat model.
