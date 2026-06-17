"""Agent that accepts session/set_config_option calls (loopback fixture)."""
from __future__ import annotations

from conduit_sdk import AgentServer

server = AgentServer(name="config-agent", version="0.0.1")

# Track the most recently set config option.
_last_config: dict[str, str] = {}


@server.on_new_session
async def new_session(params):
    return "config-test-session"


@server.on_prompt
async def echo(ctx, session_id, content):
    text = "".join(b.get("text", "") for b in content if isinstance(b, dict))
    await ctx.send_text("echo: " + text)
    return "end_turn"


def get_last_config() -> dict[str, str]:
    return dict(_last_config)


if __name__ == "__main__":
    server.run()
