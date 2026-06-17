"""Multi-server agent: calls SDK tools across two MCP servers (loopback fixture).

Restores the multi-server aggregation coverage that was lost when the old
handle_mcp_request tests were dropped: two SDK MCP servers are exposed to the
agent simultaneously and invoked in a single turn.
"""
from __future__ import annotations

import re

from conduit_sdk import AgentServer

server = AgentServer(name="multi-mcp-caller")


@server.on_prompt
async def prompt(ctx, session_id, content):
    text = "".join(b.get("text", "") for b in content if isinstance(b, dict))
    nums = [int(x) for x in re.findall(r"\d+", text)]
    words = re.findall(r"[a-zA-Z]+", text)
    parts = []
    if len(nums) >= 2:
        r = await ctx.call_tool("math", "add", {"a": nums[0], "b": nums[1]})
        parts.append(r["content"][0]["text"])
    if words:
        r2 = await ctx.call_tool("text", "upper", {"s": words[-1]})
        parts.append(r2["content"][0]["text"])
    await ctx.send_text(" ".join(parts))
    return "end_turn"


if __name__ == "__main__":
    server.run()
