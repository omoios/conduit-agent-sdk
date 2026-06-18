"""Deterministic loopback agent for the comprehensive demo (no model needed).

Two behaviors, selected by the prompt text:
  * contains "form" -> requests elicitation from the client, echoes the answer
  * otherwise       -> a full turn: a thought, two tool calls (a file read and
    a shell command) each with output, and a final text message

Because it never calls a model, the demo's feature tour is instant and stable.
"""

from conduit_sdk import AgentServer

server = AgentServer(name="demo-agent", version="1.0.0")


def _prompt_text(content) -> str:
    return " ".join(b.get("text", "") for b in content if isinstance(b, dict))


@server.on_prompt
async def handle(ctx, session_id, content):
    if "form" in _prompt_text(content).lower():
        # Ask the client for structured input, then echo what came back.
        answer = await ctx.request_elicitation(
            "What is your name?",
            requested_schema={
                "type": "object",
                "properties": {"name": {"type": "string"}},
            },
        )
        await ctx.send_text(f"Thanks — the form returned: {answer}")
        return "end_turn"

    # A representative multi-step turn.
    await ctx.send_thought("Reading the config and checking the working directory.")

    await ctx.send_update({
        "sessionUpdate": "tool_call", "toolCallId": "t1",
        "title": "Read pyproject.toml", "kind": "read", "status": "pending",
        "rawInput": {"path": "pyproject.toml"},
    })
    await ctx.send_update({
        "sessionUpdate": "tool_call_update", "toolCallId": "t1", "status": "completed",
        "content": [{"type": "content",
                     "content": {"type": "text", "text": "[project]\nname = 'conduit-agent-sdk'"}}],
    })

    await ctx.send_update({
        "sessionUpdate": "tool_call", "toolCallId": "t2",
        "title": "$ pwd", "kind": "execute", "status": "pending",
        "rawInput": {"command": "pwd"},
    })
    await ctx.send_update({
        "sessionUpdate": "tool_call_update", "toolCallId": "t2", "status": "completed",
        "content": [{"type": "content",
                     "content": {"type": "text", "text": "/home/user/conduit-agent-sdk"}}],
    })

    await ctx.send_text("Read pyproject.toml and confirmed the working directory.")
    return "end_turn"


if __name__ == "__main__":
    server.run()
