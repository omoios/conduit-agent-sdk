"""Agent that calls an SDK @tool over MCP (loopback test fixture).

During on_prompt it parses two integers from the prompt and calls the "add"
tool on the "math" MCP server the client provided at session/new, then streams
the result.
"""
from __future__ import annotations

import re

from conduit_sdk import AgentServer

server = AgentServer(name="mcp-caller")


@server.on_prompt
async def prompt(ctx, session_id, content):
    text = "".join(b.get("text", "") for b in content if isinstance(b, dict))
    nums = [int(x) for x in re.findall(r"\d+", text)]
    if len(nums) >= 2:
        result = await ctx.call_tool("math", "add", {"a": nums[0], "b": nums[1]})
        await ctx.send_text(result["content"][0]["text"])
    else:
        await ctx.send_text("no numbers found")
    return "end_turn"


if __name__ == "__main__":
    server.run()
