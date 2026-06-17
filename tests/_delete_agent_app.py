"""Agent that records session/delete calls (loopback fixture)."""
from conduit_sdk import AgentServer

server = AgentServer(name="delete-agent", version="0.0.1")

# Module-level list to track deleted session IDs.
_deleted: list[str] = []


@server.on_new_session
async def new_session(params):
    return "delete-test-session"


@server.on_prompt
async def echo(ctx, session_id, content):
    text = "".join(b.get("text", "") for b in content if isinstance(b, dict))
    await ctx.send_text("echo: " + text)
    return "end_turn"


@server.on_session_delete
async def on_delete(params):
    _deleted.append(params.get("session_id", ""))
    return {}


def get_deleted() -> list[str]:
    return list(_deleted)


if __name__ == "__main__":
    server.run()
