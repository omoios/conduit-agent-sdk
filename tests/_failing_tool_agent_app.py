"""Fixture agent that fails a tool call and leaks a secret in its output.

Used by the canonical-event verification tests:
  * the failing tool must surface as ``tool.completed`` with ``ok=False`` and a
    populated ``output`` (the old adapter always reported ``ok=True``); and
  * the ``SECRET-abc123`` token exercises redaction-before-storage.
"""

from conduit_sdk import AgentServer

server = AgentServer(name="failing-tool-agent")


@server.on_prompt
async def handle(ctx, session_id, content):
    # A tool call that fails, with output carrying a leaked secret.
    await ctx.tool_call(
        "tc1", "Risky Op", kind="execute", raw_input={"cmd": "rm -rf"}
    )
    await ctx.tool_result(
        "tc1", "failed", output="ERROR: boom — leaked token SECRET-abc123"
    )
    await ctx.send_text("turn finished despite the failure")
    return "end_turn"


if __name__ == "__main__":
    server.run()
