# AgentShield in front of the real GitHub MCP server

This example runs AgentShield as a real MCP server in front of
[github/github-mcp-server](https://github.com/github/github-mcp-server) —
GitHub's own official MCP server — so a real MCP client (Claude Desktop,
Cursor, etc.) talks to AgentShield instead of talking to GitHub MCP
directly:

```text
Claude / Cursor / MCP Client
          |
          | MCP
          v
   +--------------+
   | AgentShield  |
   | MCP Gateway  |
   +------+-------+
          |
   AuthorizationEngine
          |
   ALLOW / REVIEW / DENY
          |
          v
   GitHub MCP Server
          |
          v
      GitHub API
```

GitHub MCP itself is **not modified** — AgentShield just sits in front of it
and enforces policy before any call reaches it.

## Prerequisites

1. **Python 3.11+** (matches the project's `requires-python`).
2. [Docker](https://www.docker.com/), running. GitHub publishes the local
   server as `ghcr.io/github/github-mcp-server`.
3. A [GitHub Personal Access Token](https://github.com/settings/personal-access-tokens/new).
   Grant only what you're comfortable letting an AI tool use — for this demo
   policy, read access to a repository (and, if you want to see the
   REVIEW/DENY paths against your own data, `repo` scope) is enough. See
   GitHub MCP's own [token security guidance](https://github.com/github/github-mcp-server#handling-pats-securely).

## Installation

From the repository root:

```bash
pip install -e ".[mcp]"
```

## Set the required environment variable

AgentShield never stores your token in a file. Set it in your own shell
before starting the gateway:

```bash
export GITHUB_PERSONAL_ACCESS_TOKEN=your_token_here
```

[`gateway.yaml`](gateway.yaml) references it as `${GITHUB_PERSONAL_ACCESS_TOKEN}`
— AgentShield resolves that placeholder from its own process environment
only at the moment it launches the downstream GitHub MCP subprocess; the
value is never written to config, logs, or audit events.

**Do not** commit a `.env` file or any file containing your token to this
repository.

## AgentShield configuration

[`gateway.yaml`](gateway.yaml):

```yaml
server:
  name: github

downstream:
  command: docker
  args:
    - run
    - -i
    - --rm
    - -e
    - GITHUB_PERSONAL_ACCESS_TOKEN
    - -e
    - GITHUB_TOOLSETS
    - ghcr.io/github/github-mcp-server
  env:
    GITHUB_PERSONAL_ACCESS_TOKEN: "${GITHUB_PERSONAL_ACCESS_TOKEN}"
    GITHUB_TOOLSETS: "repos,pull_requests,secret_protection"

context:
  environment: development

policy:
  path: examples/github/policy.yaml

actor: agent
```

`GITHUB_TOOLSETS` restricts which GitHub MCP tools are exposed at all
(smaller, more predictable surface for this demo); see GitHub MCP's
[toolset docs](https://github.com/github/github-mcp-server#tool-configuration)
if you want more.

## Example policy

[`policy.yaml`](policy.yaml) uses **real tool names** from the current
GitHub MCP tool list (verify with the discovery command below — do not
assume these names stay fixed across GitHub MCP releases):

```yaml
rules:
  - name: allow-read-file-contents
    tool: get_file_contents
    outcome: allow
    risk: low
    reason: "Reading file contents is a harmless, read-only operation."

  - name: block-secret-scanning-access
    tool: "*secret*"
    outcome: deny
    risk: critical
    reason: "Access to secret scanning data is blocked by AgentShield."

  - name: block-repository-deletion
    tool: delete_repository
    outcome: deny
    risk: critical
    reason: "Repository deletion is blocked by AgentShield."

  - name: production-merge
    server: github
    tool: merge_pull_request
    context:
      environment: production
    outcome: review
    risk: high
    reason: "Production merges require human approval."
```

This demonstrates all three outcomes against real GitHub MCP tools:
`get_file_contents`/`search_repositories` → **ALLOW**,
`delete_repository`/`get_secret_scanning_alert` → **DENY**,
`merge_pull_request` when `context.environment == production` → **REVIEW**
(and, with `context.environment: development` as set in `gateway.yaml`
above, that same tool falls through to the default **ALLOW** instead —
try switching it to see the policy engine's context matching in action).

## Run it

```bash
python -m agentshield.mcp.server --config examples/github/gateway.yaml
```

Diagnostics go to stderr; stdout is reserved for MCP protocol traffic.

## Claude Desktop / Cursor / other MCP client configuration

Point the client at AgentShield, **not** at GitHub MCP directly:

```json
{
  "mcpServers": {
    "agentshield-github": {
      "command": "python",
      "args": [
        "-m", "agentshield.mcp.server",
        "--config", "/absolute/path/to/examples/github/gateway.yaml"
      ]
    }
  }
}
```

`GITHUB_PERSONAL_ACCESS_TOKEN` must be set in the environment the client
launches AgentShield with (most MCP hosts let you set per-server `env`
values in their own config if you'd rather not rely on an ambient shell
variable — see your client's docs).

The client will see GitHub MCP's real tools — names, descriptions, input
schemas — proxied through AgentShield unmodified.

## Local, offline tests

The project's normal test suite never needs any of this:

```bash
pytest -m "not integration"
# or simply:
pytest   # integration tests skip themselves automatically without credentials
```

## Optional: real integration tests

With Docker running and `GITHUB_PERSONAL_ACCESS_TOKEN` set:

```bash
pytest -m integration
```

This connects through AgentShield to the real GitHub MCP server, discovers
its real tools, executes an allowed read-only call, and verifies a
protected call is blocked before it ever reaches GitHub MCP. See
[`tests/test_github_integration.py`](../../tests/test_github_integration.py).
