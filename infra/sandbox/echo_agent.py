"""Deterministic ACP echo agent for the conduit sandbox image.

Runs a real ``conduit_sdk`` ``AgentServer`` (ACP JSON-RPC over stdio) with NO
model and NO credentials -- it just echoes a fixed line. The host SDK drives it
via ``acp_agent(["docker","run","-i","--rm","conduit-sandbox", ...])``, proving
the ACP-over-docker-stdio transport deterministically (the real, CI-safe B path).
Mirrors tests/_session_echo_agent_app.py.
"""

from __future__ import annotations

import uuid

from conduit_sdk import AgentServer

server = AgentServer(name="sandbox-echo-agent", version="0.0.1")


@server.on_new_session
async def new_session(params):
    return uuid.uuid4().hex


@server.on_prompt
async def echo(ctx, session_id, content):
    await ctx.send_text("echo from sandbox")
    return "end_turn"


@server.on_session_delete
async def on_delete(params):
    return {}


if __name__ == "__main__":
    server.run()
