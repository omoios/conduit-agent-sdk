"""Echo agent built with conduit's AgentServer (loopback test fixture)."""
from conduit_sdk import AgentServer

server = AgentServer(name="echo-agent", version="0.0.1")


@server.on_new_session
async def new_session(params):
    return "echo-session"


@server.on_prompt
async def echo(ctx, session_id, content):
    text = "".join(b.get("text", "") for b in content if isinstance(b, dict))
    await ctx.send_text("echo: " + text)
    await ctx.send_text(" (done)")
    return "end_turn"


if __name__ == "__main__":
    server.run()
