"""Agent that elicits structured input from the client (loopback fixture)."""
from conduit_sdk import AgentServer

server = AgentServer(name="elicit-agent", version="0.0.1")


@server.on_new_session
async def new_session(params):
    return "elicit-session"


@server.on_prompt
async def elicit(ctx, session_id, content):
    # Ask the client for the user's name via an unstable elicitation request.
    result = await ctx.request_elicitation(
        "What is your name?",
        requested_schema={
            "type": "object",
            "properties": {"name": {"type": "string", "title": "Name"}},
            "required": ["name"],
        },
    )
    action = result.get("action")
    if action == "accept":
        name = (result.get("content") or {}).get("name", "unknown")
        await ctx.send_text(f"hello {name}")
    elif action == "decline":
        await ctx.send_text("declined")
    else:
        await ctx.send_text("cancelled")
    return "end_turn"


if __name__ == "__main__":
    server.run()
