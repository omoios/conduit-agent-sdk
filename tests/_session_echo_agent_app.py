"""Agent that echoes its session id (loopback test fixture)."""
import uuid

from conduit_sdk import AgentServer

server = AgentServer(name="session-echo-agent", version="0.0.1")


@server.on_new_session
async def new_session(params):
    return uuid.uuid4().hex


@server.on_prompt
async def echo_session(ctx, session_id, content):
    await ctx.send_text(f"session={session_id}")
    return "end_turn"


@server.on_session_delete
async def on_delete(params):
    return {}


if __name__ == "__main__":
    server.run()
