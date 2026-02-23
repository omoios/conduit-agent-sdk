# /// script
# requires-python = ">=3.12"
# dependencies = ["conduit-agent-sdk"]
# ///
"""10 — Hooks: Pre/Post tool-use hooks with audit logging.

Demonstrates the lifecycle hook system for intercepting agent
operations. Hooks can inspect, modify, or block tool calls.

    uv run examples/10_hooks.py
"""

import asyncio
import json
from datetime import datetime, timezone

from conduit_sdk import Client, HookType


async def main():
    client = await Client.from_registry("claude-acp")

    # Register a PreToolUse hook — runs before every tool call.
    @client.hooks.on(HookType.PreToolUse)
    async def audit_log(ctx):
        tool_name = ctx.get("tool_name")
        tool_input = ctx.get("tool_input")
        ts = datetime.now(timezone.utc).isoformat()
        print(f"[AUDIT {ts}] Tool: {tool_name}, Input: {json.dumps(tool_input)[:100]}")
        return ctx

    # Register a PostToolUse hook — runs after every tool call.
    @client.hooks.on(HookType.PostToolUse)
    async def log_result(ctx):
        tool_name = ctx.get("tool_name")
        print(f"[POST] {tool_name} completed")
        return ctx

    async with client:
        async for msg in client.prompt("Read the file at /tmp/test.txt"):
            print(msg.text())


if __name__ == "__main__":
    asyncio.run(main())
