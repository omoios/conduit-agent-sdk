"""Test agent that leaks a long-lived grandchild holding the ACP stdout pipe.

This reproduces the teardown pathology: an agent spawns a child that inherits
stdout (the ACP write pipe). If the Client only kills the direct agent process,
the grandchild keeps the pipe open, the connection never EOFs, and teardown
hangs. With process-group kill it dies too and teardown is clean.
"""

import subprocess

from conduit_sdk import AgentServer

# Inherits stdin/stdout/stderr by default -> holds the ACP stdout pipe open
# for far longer than any test. It writes nothing, so the ACP stream is intact.
subprocess.Popen(["sleep", "300"])

server = AgentServer(name="pipe-holder")


@server.on_prompt
async def answer(ctx, session_id, content):
    await ctx.send_text("ok")
    return "end_turn"


if __name__ == "__main__":
    server.run()
