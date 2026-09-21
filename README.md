# AgentShield

**Policy enforcement for AI agents and their tools.**

AgentShield decides whether an action an agent wants to take should be
**allowed**, sent for **review**, or **denied** — deterministically, based on
rules you define, before the action is executed.

## Why it exists

AI agents increasingly call tools that have real-world side effects: merging
pull requests, deleting resources, reading secrets, moving money. Provider
SDKs and MCP servers give you the *mechanism* to call these tools, but not a
consistent, auditable way to decide *whether a given call should be allowed*.
AgentShield is that decision layer — independent of any specific LLM
provider, agent framework, or tool protocol.

**AgentShield does not replace MCP servers. It sits in front of them.**

```text
Agent -> AgentShield -> Policy -> ALLOW / REVIEW / DENY -> Tool
```

## Architecture

```text
                    AgentShield
                         │
                  ┌──────┴──────┐
                  │     Core     │   ← Phase 1
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
         Python SDK            MCP Gateway      ← Phase 2 (this repo)
              │                     │
         Developers          AI Agents / MCP
```

The **Core** (`agentshield.decision`, `.policy`, `.engine`, `.risk`,
`.audit`) is a small, dependency-light Python library. It is deterministic:
it never calls an external service, never calls an LLM, and never executes
the action it is authorizing. Given a policy and a request, it always
returns the same decision. The Core has **no dependency on MCP** and remains
fully usable on its own.

The **MCP Gateway** (`agentshield.mcp`, Phase 2) is a policy-enforcement
proxy in front of a downstream MCP server:

```text
Agent
  |
  v
 MCP
  |
  v
AgentShield
  |
  +--> Policy Engine
  +--> Risk
  +--> Approval
  +--> Audit
  |
  v
MCP Server / Tool
```

```text
AI Agent / MCP Client
        |
        | MCP tool call
        v
+----------------------+
|   AgentShield        |
|    MCP Gateway       |
+----------+-----------+
           |
           v
   AuthorizationEngine
           |
    +------+------+
    |             |
  ALLOW        REVIEW / DENY
    |             |
    v             +-----> approval / block
Downstream MCP
   Server
```

**MCP is the transport/tool interface. AgentShield is the authorization
boundary.** The gateway depends on the Core; the Core has no idea MCP
exists. AgentShield works with existing MCP servers without requiring those
servers to be modified, and preserves their tool definitions and arguments
as-is — it is a policy layer, not a schema transformation layer.

## Minimal example (Core only)

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
configurable yet.

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

**A deterministic `DENY` must never be overridden by another layer.** Future
providers (for example, an LLM-based reasoning layer such as "Jev") may add
context, explanations, or additional review — but they cannot flip a
policy-level `DENY` into an `ALLOW`. This holds both in the Core (the
engine's precedence rules operate purely over policy rules, and nothing
exposes a way to override a returned `Decision`) and in the MCP Gateway (a
DENY is never sent to an `ApprovalProvider` and never reaches the downstream
server). Any future integration that wants to add a "second opinion" must be
additive (e.g. escalating `REVIEW` to `DENY`), never permissive.

## MCP Gateway (Phase 2)

`agentshield.mcp` puts a policy-enforcement proxy between an MCP client (an
agent) and a downstream MCP server launched locally over stdio, using the
official [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk).

```python
import asyncio
from agentshield.mcp import GatewayConfig, MCPGateway

async def main():
    config = GatewayConfig.from_yaml("examples/gateway.yaml")
    gateway = MCPGateway.from_config(config)

    async with gateway:
        tools = await gateway.list_tools()          # unmodified downstream tool defs
        result = await gateway.call_tool("echo", {"message": "hi"})
        print(result.decision.outcome, result.executed)

asyncio.run(main())
```

Install the optional MCP dependency first: `pip install -e ".[mcp]"`.

### Configuration

```yaml
server:
  name: demo

downstream:
  command: python
  args:
    - examples/mcp_server.py

context:
  environment: production

policy:
  path: examples/mcp_policy.yaml

actor: agent
```

`downstream` describes how to launch the downstream MCP server as a local
subprocess. `context` is static context merged into every
`AuthorizationRequest` built by this gateway (so a rule like `context:
{environment: production}` works through the gateway exactly as it does in
the Core). `actor` defaults to `"agent"`.

### Request mapping

Every intercepted `tools/call` is mapped onto the Core's
`AuthorizationRequest` like this:

| MCP call | `AuthorizationRequest` field |
|---|---|
| configured `actor` (default `"agent"`) | `actor` |
| configured `server.name` | `server` |
| MCP tool name | `tool` |
| MCP tool arguments, unmodified | `arguments` |
| configured static `context` | `context` |

### ALLOW / REVIEW / DENY at the gateway

* **ALLOW** — the call is forwarded to the downstream server unmodified;
  its result is returned to the caller.
* **DENY** — the downstream server is **never called**. The gateway returns
  a `GatewayCallResult` with `executed=False` and the decision (rule, risk,
  reason) attached — no arguments or internal details are leaked back.
* **REVIEW** — the call is **not** executed automatically. It is resolved
  through an `ApprovalProvider`:

  ```python
  from agentshield.mcp import ApprovalProvider, ApprovalResult

  class MyApprovalProvider(ApprovalProvider):
      async def request_approval(self, request, decision) -> ApprovalResult:
          ...  # ask Slack / a web UI / a human, then:
          return ApprovalResult(approved=True, approver="alice")
  ```

  Two minimal implementations ship out of the box: `CallbackApprovalProvider`
  (wraps a sync or async callable — the main building block for tests and
  programmatic integrations) and `ConsoleApprovalProvider` (prompts a human
  at the terminal; demo use only). If a REVIEW decision is reached and no
  `ApprovalProvider` is configured, the gateway fails closed and raises
  `ApprovalProviderRequiredError` — it never guesses.

### Audit

The gateway reuses the Core's `AuditLog` — every intercepted call, of every
outcome, produces one `AuditEvent`. Two optional, backward-compatible fields
were added to `AuditEvent` for the gateway's use: `approval_required` and
`approval_outcome` (`"approved"` / `"rejected"` / `None`). Core-only usage is
unaffected; these default to `False` / `None`.

### Error handling

`agentshield.mcp` defines its own exception hierarchy (`GatewayError` and
subclasses) for gateway/transport failures, distinct from Core errors like
`PolicyError`: `DownstreamConnectionError`, `DownstreamToolError`,
`AuthorizationEvaluationError` (the engine itself failed — fails closed,
never forwards), `ApprovalProviderRequiredError`, `ApprovalProviderError`,
`GatewayConfigError`. Downstream errors are never swallowed.

## Local demo

[`examples/mcp_server.py`](examples/mcp_server.py) is a tiny fake MCP server
with three harmless tools (`echo`, `create_file`, `delete_file`), confined to
a local sandbox directory. [`examples/mcp_policy.yaml`](examples/mcp_policy.yaml)
allows `echo`, requires review for `create_file`, and denies `delete_file`.

```bash
pip install -e ".[mcp]"
python examples/run_demo.py
```

This runs `MCP Client -> AgentShield -> Fake MCP Server` end to end and
prints one ALLOW, one REVIEW (auto-approved for the demo), one DENY, and the
resulting audit trail.

## What's implemented

* **Phase 1 — Core**: typed decision/request/policy models, deterministic
  matching and precedence, a default-allow fallback, and an in-memory audit
  log.
* **Phase 2 — MCP Gateway**: a policy-enforcement proxy for a downstream MCP
  server reached over stdio, using the official MCP SDK; tool discovery;
  ALLOW/REVIEW/DENY enforcement; a pluggable approval abstraction; audit
  logging.

Deliberately **not** implemented yet: a Python SDK, a CLI, an HTTP server, a
database, a web dashboard, authentication, an LLM-based reasoning provider
("Jev"), or any integration with a specific tool ecosystem (GitHub, AWS,
ComfyUI, ...).

## Roadmap

```text
Core (Phase 1)
→ MCP Gateway (Phase 2, this repo)
→ Python SDK
→ Jev provider
→ TypeScript SDK
→ integrations
```

## Installing and running tests

```bash
pip install -e ".[dev]"   # includes the optional mcp extra
pytest
```

## Status

AgentShield is an early-stage, open-source project. It does not make any
production security guarantees; treat it as policy infrastructure you
integrate and test against your own threat model.
