"""Local end-to-end demo: MCP client -> AgentShield -> fake MCP server.

Run from the repository root, with the project installed including the
optional "mcp" extra:

    pip install -e ".[mcp]"
    python examples/run_demo.py

Demonstrates all three outcomes against examples/mcp_policy.yaml:
  * echo        -> ALLOW   (forwarded to the downstream server)
  * create_file -> REVIEW  (held for approval; auto-approved here for the demo)
  * delete_file -> DENY    (blocked before it ever reaches the server)
"""

from __future__ import annotations

import asyncio
import pathlib

from agentshield.mcp import ApprovalResult, CallbackApprovalProvider, GatewayConfig, MCPGateway

EXAMPLES_DIR = pathlib.Path(__file__).parent


def _auto_approve(request, decision) -> ApprovalResult:
    print(f"  [approval] auto-approving REVIEW for '{request.action}' (demo only)")
    return ApprovalResult(approved=True, approver="demo-auto-approver")


async def main() -> None:
    config = GatewayConfig.from_yaml(str(EXAMPLES_DIR / "gateway.yaml"))
    gateway = MCPGateway.from_config(
        config, approval_provider=CallbackApprovalProvider(_auto_approve)
    )

    async with gateway:
        tools = await gateway.list_tools()
        print(f"Discovered downstream tools: {[t.name for t in tools]}\n")

        print("--- echo('hello from AgentShield') ---")
        echo_result = await gateway.call_tool("echo", {"message": "hello from AgentShield"})
        print(f"decision: {echo_result.decision.outcome.value} ({echo_result.decision.reason})")
        print(f"executed: {echo_result.executed}")
        if echo_result.result is not None:
            print(f"downstream result: {echo_result.result.content}")

        print("\n--- create_file('note.txt') ---")
        review_result = await gateway.call_tool(
            "create_file", {"name": "note.txt", "content": "hello"}
        )
        print(
            f"decision: {review_result.decision.outcome.value} ({review_result.decision.reason})"
        )
        print(f"approval: {review_result.approval}")
        print(f"executed: {review_result.executed}")
        if review_result.result is not None:
            print(f"downstream result: {review_result.result.content}")

        print("\n--- delete_file('anything.txt') ---")
        deny_result = await gateway.call_tool("delete_file", {"name": "anything.txt"})
        print(f"decision: {deny_result.decision.outcome.value} ({deny_result.decision.reason})")
        print(f"executed: {deny_result.executed}")

    print(f"\nAudit events recorded: {len(gateway.audit_log.events)}")
    for event in gateway.audit_log.events:
        print(
            f"  [{event.timestamp.isoformat()}] action={event.request.action!r} "
            f"outcome={event.decision.outcome.value} rule={event.decision.rule!r} "
            f"approval_required={event.approval_required} "
            f"approval_outcome={event.approval_outcome!r}"
        )


if __name__ == "__main__":
    asyncio.run(main())
