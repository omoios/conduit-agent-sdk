"""Rich agent fixture for in-depth e2e tests.

Emits a representative agent turn over the real ACP wire — a thought, a tool
call with its result, and a final text message — so loopback e2e tests can
assert the SDK surfaces the full update stream (streaming, toolview, run layer).
The behavior is deterministic (no model), so these tests are fast and stable.
"""

from conduit_sdk import AgentServer

server = AgentServer(name="rich-agent")


@server.on_prompt
async def handle(ctx, session_id, content):
    # 1) a reasoning/thought chunk
    await ctx.send_thought("Planning: I'll read the config file.")

    # 2) a tool call (read) + its completion with output content
    await ctx.send_update(
        {
            "sessionUpdate": "tool_call",
            "toolCallId": "tc1",
            "title": "Read config.txt",
            "kind": "read",
            "status": "pending",
            "rawInput": {"path": "config.txt"},
        }
    )
    await ctx.send_update(
        {
            "sessionUpdate": "tool_call_update",
            "toolCallId": "tc1",
            "status": "completed",
            "content": [
                {"type": "content", "content": {"type": "text", "text": "port = 8080"}}
            ],
        }
    )

    # 3) the final assistant message
    await ctx.send_text("The config sets port 8080.")
    return "end_turn"


if __name__ == "__main__":
    server.run()
