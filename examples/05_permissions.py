# /// script
# requires-python = ">=3.12"
# dependencies = ["conduit-agent-sdk"]
# ///
"""05 — Permissions: Custom tool-use permission callbacks.

Demonstrates how to use ``AgentOptions.can_use_tool`` to approve
or deny tool use requests from the agent.

    uv run examples/05_permissions.py
"""

import asyncio

from conduit_sdk import (
    AgentOptions,
    Client,
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
)


async def my_policy(
    tool_name: str,
    tool_input: str,
    context: ToolPermissionContext,
) -> PermissionResultAllow | PermissionResultDeny:
    """Allow read-only tools, deny shell access."""
    if tool_name in ("Read", "Glob", "Grep"):
        return PermissionResultAllow()

    if tool_name == "Bash":
        return PermissionResultDeny("Shell access is not allowed")

    # Everything else — allow by default.
    return PermissionResultAllow()


async def main():
    client = await Client.from_registry(
        "claude-acp",
        options=AgentOptions(
            can_use_tool=my_policy,
            permission_mode="default",
            model="claude-sonnet-4-20250514",
        ),
    )

    async with client:
        async for msg in client.prompt("Refactor main.py"):
            print(msg.text())


if __name__ == "__main__":
    asyncio.run(main())
