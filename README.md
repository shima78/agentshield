# AgentShield

**A policy-driven decision engine for AI agents and AI-native applications.**

AgentShield decides whether an action an agent wants to take should be
**allowed**, sent for **review**, or **denied** — deterministically, based on
rules you define. **AgentShield decides; the agent/application executes.**
It does not need to sit between an agent and a tool, an MCP server, or
anything else — it only needs to be consulted before the action happens:

```text
AI Agent
   |
   | evaluate action
   v
AgentShield
   |
   | ALLOW / REVIEW / DENY
   v
AI Agent
   |
   | execute if permitted
   v
Tool / MCP / API / Action
```

```python
decision = shield.evaluate(request)
```

> **Scope note:** in this phase, AgentShield is a *decision engine*, not an
> enforcement mechanism. It cannot, by itself, technically stop a malicious
> or misbehaving agent from ignoring its answer and executing the action
> anyway — that requires actually sitting in the execution path (as the MCP
> Gateway/server below optionally does) or another enforcement integration.
> Making that guarantee robust across execution paths is future work.

## Why it exists

AI agents increasingly want to take actions with real-world side effects:
merging pull requests, deleting resources, reading secrets, moving money.
Provider SDKs and tool protocols like MCP give you the *mechanism* to take
these actions, but not a consistent, auditable way to decide *whether a
given action should be allowed*. AgentShield is that decision layer —
independent of any specific LLM provider, agent framework, or tool
protocol.

## Two ways to use it

* **Python SDK / Library (primary)** — embed the Core decision engine
  directly in your own agent/application code and consult it before
  executing an action. See [`examples/sdk_example.py`](examples/sdk_example.py)
  and "Minimal example" below. No MCP dependency required.
* **MCP adapter (optional integration)** — `agentshield.mcp` is one
  integration built on top of the same Core: it places AgentShield in the
  execution path between an MCP client and a downstream MCP server, so it
  can also *enforce* (not just advise on) ALLOW/REVIEW/DENY for that path.
  See "MCP adapter" below. This is not what AgentShield *is* — it's one way
  to use it.

## Architecture

```text
                    AgentShield
                         │
                  ┌──────┴──────┐
                  │     Core     │   ← the decision engine
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
         Python SDK            MCP adapter      ← agentshield.mcp (optional)
              │                     │
         Developers          AI Agents / MCP
```

The **Core** (`agentshield.decision`, `.policy`, `.engine`, `.risk`,
`.audit`) is a small, dependency-light Python library. It is deterministic:
it never calls an external service, never calls an LLM, and never executes
the action it decides on — it only returns a `Decision`. Given a policy and
a request, it always returns the same decision. The Core has **no
dependency on MCP** (see `tests/test_core_independence.py`) and remains
fully usable on its own.

```text
Core
  ^
  |
MCP adapter (agentshield.mcp)
```

The dependency direction is one-way: the Core has no idea MCP, or anything
else that might call it, exists.

### MCP is one adapter among others

`agentshield.mcp` (documented in full below) is an **optional** integration
that happens to place AgentShield in the execution path for MCP traffic
specifically, so it can enforce rather than just advise for that path:

```text
AI Agent / MCP Client
        |
        | MCP tool call
        v
+----------------------+
|   AgentShield        |
|    MCP adapter       |
+----------+-----------+
           |
           v
    DecisionEngine
           |
    +------+------+
    |             |
  ALLOW        REVIEW / DENY
    |             |
    v             +-----> approval / block
Downstream MCP
   Server
```

AgentShield works with existing MCP servers without requiring those servers
to be modified, and preserves their tool definitions and arguments as-is —
it is a policy layer, not a schema transformation layer. But this is one
possible caller of the Core, not a requirement: the same `DecisionEngine`
works identically for an HTTP API call, a shell command, a workflow step,
or anything else expressed as a `DecisionRequest`.

## Minimal example (Core only, no MCP)

```python
from agentshield import DecisionEngine, DecisionRequest, Outcome, Policy

policy = Policy.from_yaml("examples/policy.yaml")
shield = DecisionEngine(policy)

decision = shield.evaluate(
    DecisionRequest(
        action="merge_pull_request",
        actor="agent",
        server="github",
        arguments={"repo": "acme/app", "pull_request": 42},
        context={"environment": "production"},
    )
)

if decision.outcome == Outcome.DENY:
    ...  # the agent must not execute the action
elif decision.outcome == Outcome.REVIEW:
    ...  # the agent must route this through its own approval flow first
else:
    ...  # the agent may proceed to actually perform the action, however
         # it chooses to (MCP, a direct API call, a CLI, ...)

print(decision.outcome)   # Outcome.REVIEW
print(decision.allowed)   # False
print(decision.reason)    # "Production merges require human approval."
print(decision.rule)      # "production-merge"
```

See [`examples/sdk_example.py`](examples/sdk_example.py) for a runnable
version of this.

> **Breaking rename:** what was `AuthorizationEngine`/`AuthorizationRequest`
> is now `DecisionEngine`/`DecisionRequest`, reflecting that this is a
> general-purpose decision engine, not an MCP-specific authorization layer.
> `DecisionRequest`'s tool/action field was renamed from `tool` to `action`.
> The old class names remain importable as deprecated aliases
> (`AuthorizationEngine is DecisionEngine`, etc.) so `from agentshield import
> AuthorizationEngine` still works, but any code constructing the request
> with `tool=...` must change to `action=...`. `PolicyRule.tool` (the policy
> YAML field) is **unchanged** — existing policy files keep working as-is.

## Using AgentShield from an Agent

The pattern above is the whole integration surface: a Python agent (with
or without a framework) calls `shield.evaluate(request)` directly, as a
decision service/library, **before** doing anything else. AgentShield is
not an execution proxy — it never sits between the agent and whatever it
would eventually call (a tool, an MCP server, a deploy script). It only
needs to be asked first:

```python
decision = shield.evaluate(request)

if decision.outcome == Outcome.DENY:
    stop()
elif decision.outcome == Outcome.REVIEW:
    review()
else:
    execute()
```

See [`examples/agent_demo.py`](examples/agent_demo.py) for a complete,
runnable version of this: a tiny "agent" function proposes a production
deployment, prints what deterministic policy (and, if configured, Jev
semantic evaluation) decided, and only prints a simulated
`🚀 Deploying version 2.4.1...` when the decision actually permits it —
a `REVIEW` or `DENY` decision stops the demo before that point.

```bash
python examples/agent_demo.py                    # deterministic policy only
TYPESAFE_API_KEY=... python examples/agent_demo.py  # + real Jev semantic evaluation
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

**A deterministic `DENY` must never be overridden by another layer.** An
optional semantic evaluator (see "Semantic evaluation" below) may add
context or additional review — but it cannot flip a policy-level `DENY`
into an `ALLOW`, and `DecisionEngine` never even consults it once policy
has already said DENY. This holds both in the Core (the engine's
precedence rules operate purely over policy rules, and nothing exposes a
way to override a returned `Decision`) and in the MCP Gateway (a DENY is
never sent to an `ApprovalProvider` and never reaches the downstream
server). Any layer that wants to add a "second opinion" must be additive
(e.g. escalating `ALLOW` to `REVIEW`), never permissive.

## Semantic evaluation (optional, via Jev)

Deterministic policy answers "is this technically permitted?" `DecisionEngine`
can optionally also ask a **semantic** question: "given the action, its
arguments, the current context, and the applicable policy, is this
actually a *sensible* decision?" — a technically-permitted action can
still be a bad idea (a database migration proposed for Friday evening in
production, say).

```text
Action + Context + Applicable Policy
                ↓
              Jev
                ↓
      semantic assessment
                ↓
          AgentShield
```

`agentshield.jev.JevSemanticEvaluator` implements this using
[Jev](https://docs.typesafe.ai), TypeSafe's "System One" model, via the
official `typesafe-sdk` package (optional dependency: `pip install -e ".[jev]"`,
`export TYPESAFE_API_KEY=...`). It asks a single structured `Choice`
question (`good` / `review` / `bad`) with only the *relevant* slice of
policy for that one request as context — never the whole policy file.

```python
from agentshield import DecisionEngine, DecisionRequest, Policy
from agentshield.jev import JevSemanticEvaluator

shield = DecisionEngine(Policy.from_dict({"rules": []}), semantic_evaluator=JevSemanticEvaluator())

decision = shield.evaluate(
    DecisionRequest(
        action="deploy",
        actor="release-agent",
        context={
            "environment": "production",
            "time": "friday_evening",
            "database_migration": True,
        },
    )
)
```

**Deterministic policy remains authoritative.** `DecisionEngine` never
consults the semantic evaluator for a request that deterministic policy
already denies. When it is consulted, its verdict can only ever escalate
an `ALLOW` toward `REVIEW` — it can never produce `DENY`, and never
downgrades an existing `REVIEW`. `Decision.confidence` is set from Jev's
own confidence score when a semantic evaluation ran (deterministic-only
decisions keep `confidence = 1.0`, as before).

This is entirely optional: `agentshield.jev` is never imported by the Core
or by `DecisionEngine` itself (see `tests/test_core_independence.py`), and
Jev is not an enforcement mechanism or a security guarantee — it is one
more input into a decision the agent/application is still responsible for
acting on. See [`examples/jev_example.py`](examples/jev_example.py).

## MCP adapter

An **optional integration**, not the definition of AgentShield: `agentshield.mcp`
puts a policy-enforcement proxy between an MCP client (an agent) and a
downstream MCP server launched locally over stdio, using the official
[MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk).

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
`DecisionRequest` built by this gateway (so a rule like `context:
{environment: production}` works through the gateway exactly as it does in
the Core). `actor` defaults to `"agent"`.

### Request mapping

Every intercepted `tools/call` is mapped onto the Core's generic
`DecisionRequest` like this — this is the adapter's one job, translating
MCP's vocabulary into the Core's generic vocabulary:

| MCP call | `DecisionRequest` field |
|---|---|
| configured `actor` (default `"agent"`) | `actor` |
| configured `server.name` | `server` |
| MCP tool name | `action` |
| MCP tool arguments, unmodified | `arguments` |
| configured static `context` | `context` |

### ALLOW / REVIEW / DENY at the gateway

* **ALLOW** — the call is forwarded to the downstream server unmodified;
  its result is returned to the caller exactly as the downstream server
  produced it — **including** a downstream-reported error
  (`CallToolResult.is_error=True`, e.g. "file not found"). That is a normal
  MCP result, not a gateway failure, so it is never turned into a Python
  exception; only a call that could not be carried out at all (a broken
  connection, a malformed protocol response) raises `DownstreamToolError`.
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

## Running AgentShield as a real MCP server

`agentshield.mcp.server` is a thin adapter that exposes an `MCPGateway` as a
real, upstream-facing MCP server over stdio, using the official MCP SDK's
`Server`/`stdio_server`. This is the case where the MCP adapter actually
sits in the execution path and can enforce, not just advise. It contains
**no decision logic of its own** — every `tools/call` is routed straight
through the same `MCPGateway.call_tool()` used by the library form above:

```text
MCP Client / AI Agent
         |
         | MCP / stdio
         v
+----------------------+
|     AgentShield      |
|      MCP Server      |   agentshield.mcp.server — protocol only
+----------+-----------+
           |
           v
      MCPGateway          agentshield.mcp.gateway — MCP adapter
           |
           v
  DecisionEngine           agentshield Core — MCP-agnostic
           |
    +------+------+------+
    |             |      |
  ALLOW         REVIEW  DENY
    |             |      |
    |          approval STOP
    |             |
    +-------------+
           |
           v
  DownstreamMCPProxy -> Downstream MCP Server
```

Start it:

```bash
pip install -e ".[mcp]"
python -m agentshield.mcp.server --config examples/gateway.yaml
```

MCP protocol traffic uses stdout; all diagnostics go to stderr via the
standard `logging` module, so stdout stays clean for the protocol. The
downstream connection is established once at startup and kept alive for the
life of the upstream session; it is always closed on shutdown, including on
error, so no subprocess is leaked.

**No `ApprovalProvider` is wired up by this CLI launcher.** stdin/stdout in
this process are owned by the MCP protocol stream, so the interactive
`ConsoleApprovalProvider` cannot be used here — a REVIEW decision therefore
fails closed (the client gets a clear "no approval provider configured"
result, and nothing is forwarded downstream). To approve REVIEW calls,
embed `AgentShieldMCPServer` directly and pass a `CallbackApprovalProvider`
backed by Slack, a web UI, a queue, etc. — see
[`tests/test_mcp_server.py`](tests/test_mcp_server.py) for a worked example.

### Using it from Claude Desktop / Cursor / other MCP clients

Add it to the client's MCP server configuration, e.g. Claude Desktop's
`claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "agentshield": {
      "command": "python",
      "args": [
        "-m", "agentshield.mcp.server",
        "--config", "/absolute/path/to/examples/gateway.yaml"
      ]
    }
  }
}
```

The client then sees exactly the downstream server's tools (names,
descriptions, input schemas, unmodified) and every call it makes is
authorized by policy before AgentShield forwards it.

### ALLOW / REVIEW / DENY through the real server

Against [`examples/mcp_policy.yaml`](examples/mcp_policy.yaml):

```text
echo(...)          -> ALLOW  -> forwarded; downstream result returned as-is
create_file(...)   -> REVIEW -> blocked (no approval provider configured)
delete_file(...)   -> DENY   -> blocked; downstream never called
```

## Local demos

```bash
# Core only, no MCP: the primary usage pattern.
python examples/sdk_example.py

# A tiny "agent" that consults AgentShield before deploying, then only
# simulates the deploy if the decision permits it.
python examples/agent_demo.py

# Same, with real Jev semantic evaluation added on top.
pip install -e ".[jev]"
TYPESAFE_API_KEY=... python examples/agent_demo.py
```

The two demos below need the optional MCP extra:
[`examples/mcp_server.py`](examples/mcp_server.py) is a tiny fake MCP server
with three harmless tools (`echo`, `create_file`, `delete_file`), confined to
a local sandbox directory. [`examples/mcp_policy.yaml`](examples/mcp_policy.yaml)
allows `echo`, requires review for `create_file`, and denies `delete_file`.
All demos are entirely local — no network access, no API keys, no external
services.

```bash
pip install -e ".[mcp]"

# MCP library form: embeds MCPGateway directly in this process.
python examples/run_demo.py

# MCP real server form: a real MCP client connects to
# `python -m agentshield.mcp.server` as a subprocess, exactly like Claude
# Desktop or Cursor would.
python examples/run_demo_server.py
```

Both MCP demos run `MCP Client -> AgentShield -> Fake MCP Server` end to end
and demonstrate ALLOW, REVIEW, and DENY plus (for `run_demo.py`) the
resulting audit trail.

## Real MCP Integration

The MCP demos above are proven against a small fake local MCP server.
[`examples/github/`](examples/github/) proves the exact same, unmodified
MCP adapter against a **real** MCP ecosystem server:
[github/github-mcp-server](https://github.com/github/github-mcp-server),
GitHub's own official MCP server.

```text
Claude
  ↓
AgentShield
  ↓
GitHub MCP
  ↓
GitHub
```

Why this matters: AgentShield can sit in front of an **existing, unmodified
MCP server** — this isn't limited to a purpose-built demo server. The same
`MCPGateway`/`AgentShieldMCPServer` that talk to `examples/mcp_server.py`
talk to real GitHub MCP with zero code changes; only the config and policy
differ. Nothing GitHub-specific was added to the Core, `MCPGateway`, or
`AgentShieldMCPServer` — GitHub-specific detail lives only in
[`examples/github/`](examples/github/) and
[`tests/test_github_integration.py`](tests/test_github_integration.py). The
same gateway works unchanged in front of ComfyUI MCP, AWS MCP, or any other
MCP server.

**Security model:** AgentShield is positioned between the agent and the
tool layer. It does not replace MCP, and it does not modify the downstream
MCP server — it evaluates the action *before* execution:

```text
Agent
  ↓
AgentShield
  ↓
Policy decision
  ↓
Tool execution
```

So `DENY` = the tool is never executed, whether that tool is a local fake
server or the real GitHub API.

See **[`examples/github/README.md`](examples/github/README.md)** for full,
step-by-step setup: prerequisites, the Python 3.11+ requirement,
installation, GitHub MCP setup (Docker), the required
`GITHUB_PERSONAL_ACCESS_TOKEN` environment variable (never committed —
referenced from config as `${GITHUB_PERSONAL_ACCESS_TOKEN}` and resolved
only at connect time), the example policy and gateway config, an MCP client
configuration example, and the local-test vs. integration-test commands.

Quick summary:

```bash
pip install -e ".[mcp]"
export GITHUB_PERSONAL_ACCESS_TOKEN=your_token_here   # never committed
python -m agentshield.mcp.server --config examples/github/gateway.yaml
```

The normal test suite stays completely offline and credential-free:

```bash
pytest                      # integration tests skip themselves automatically
pytest -m "not integration" # same, explicit
```

Real integration tests (require Docker + a token):

```bash
pytest -m integration
```

## What's implemented

* **Core — decision engine**: `DecisionEngine`/`DecisionRequest`, typed
  decision/policy models, deterministic matching and precedence, a
  default-allow fallback, and an in-memory audit log. This is the primary,
  MCP-agnostic public API (`shield.evaluate(request)`).
* **MCP adapter (library)**: `MCPGateway`, a policy-enforcement proxy for a
  downstream MCP server reached over stdio, using the official MCP SDK;
  tool discovery; ALLOW/REVIEW/DENY enforcement; a pluggable approval
  abstraction; audit logging.
* **MCP adapter (real server)**: `agentshield.mcp.server` exposes
  `MCPGateway` as an actual upstream-facing MCP server over stdio (a thin
  protocol adapter with no decision logic of its own), launchable via
  `python -m agentshield.mcp.server --config ...` and usable directly from
  Claude Desktop, Cursor, or any other MCP-compatible client.
* **Real MCP integration**: the same MCP adapter proven against a real MCP
  ecosystem server (GitHub MCP) rather than only the bundled fake one, with
  an opt-in, credential-gated integration test suite.
* **Optional semantic evaluation**: `DecisionEngine(..., semantic_evaluator=...)`
  and `agentshield.jev.JevSemanticEvaluator`, a single `good`/`review`/`bad`
  judgment on top of deterministic policy, backed by the real Jev API. The
  Core has no dependency on it either.

Deliberately **not** implemented yet: automatic agent interception, a
general CLI, an HTTP authorization service or remote AgentShield service, a
database, a web dashboard, authentication infrastructure, multiple semantic
questions/evaluators/models, a semantic-orchestration or confidence-threshold
framework, or configurable approval backends (Slack, webhook, web UI, secret
management). A full enforcement/proxy redesign beyond the existing MCP
adapter is future work — see the scope note at the top of this README.

## Roadmap

```text
Core decision engine (current)
→ MCP adapter — library + real server (this repo)
→ Real MCP integration — GitHub (this repo)
→ Optional semantic evaluation — Jev (this repo)
→ Python SDK packaging
→ stronger enforcement integrations
→ TypeScript SDK
→ further integrations
```

## Installing and running tests

Requires **Python 3.11+** (`requires-python = ">=3.11"`).

```bash
pip install -e ".[dev]"       # includes the optional mcp and jev extras
pytest                        # offline, credential-free
pytest -m integration         # optional: real GitHub MCP + real Jev API tests
```

## Status

AgentShield is an early-stage, open-source project. It does not make any
production security guarantees; treat it as policy infrastructure you
integrate and test against your own threat model.
