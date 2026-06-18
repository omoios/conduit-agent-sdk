"""Emit-agent fixture for testing AgentContext typed emit helpers.

Emits a deterministic turn using every new helper (tool_call, tool_result,
plan, usage, mode_change) over the real ACP wire, so loopback tests can
assert the client surfaces matching SessionUpdates.
"""

from conduit_sdk import AgentServer

server = AgentServer(name="emit-agent")


@server.on_prompt
async def handle(ctx, session_id, content):
    # 1) tool call + its result
    await ctx.tool_call("tc1", "Search", kind="search", raw_input={"q": "x"})
    await ctx.tool_result("tc1", "completed", output="found 3 hits")

    # 2) plan
    await ctx.plan([{"content": "g1", "priority": "high", "status": "in_progress"}])

    # 3) usage
    await ctx.usage(used=100, size=200)

    # 4) mode change
    await ctx.mode_change("build")

    # 5) final text
    await ctx.send_text("done")
    return "end_turn"


if __name__ == "__main__":
    server.run()
