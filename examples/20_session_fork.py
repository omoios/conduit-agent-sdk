# /// script
# requires-python = ">=3.12"
# dependencies = ["conduit-agent-sdk"]
# ///
"""20 — Session Fork: Fork a session to explore different paths.

Demonstrates forking a session to create a new session that shares
the conversation history up to the fork point. Each fork can then
continue independently, useful for exploring alternatives.

    uv run examples/20_session_fork.py
"""

import asyncio

from conduit_sdk import Client


async def main():
    client = Client(["claude", "--agent"])

    async with client:
        # Create an initial session.
        session = await client.new_session()
        print(f"Original session: {session.session_id}\n")

        # First turn - establish some context.
        print("--- Original Session: Turn 1 ---")
        messages = await session.prompt(
            "I'm building a REST API in Python. What framework should I use?"
        )
        for msg in messages:
            print(msg.text())

        # Fork the session at this point.
        print("\n--- Forking Session ---")
        forked_session = await session.fork()
        print(f"Forked session: {forked_session.session_id}\n")

        # Continue original session with FastAPI focus.
        print("--- Original Session: Turn 2 (FastAPI focus) ---")
        messages = await session.prompt(
            "Great, I'll use FastAPI. Show me a basic hello world endpoint."
        )
        for msg in messages:
            print(msg.text())

        # The forked session has the same history but can go a different direction.
        print("\n--- Forked Session: Turn 2 (Flask focus) ---")
        messages = await forked_session.prompt(
            "Actually, I prefer Flask. Show me a basic hello world endpoint."
        )
        for msg in messages:
            print(msg.text())

        # Both sessions maintain independent histories from the fork point.
        print("\n--- Original Session: Turn 3 ---")
        messages = await session.prompt("Add a POST endpoint to this.")
        for msg in messages:
            print(msg.text())

        print("\n--- Forked Session: Turn 3 ---")
        messages = await forked_session.prompt("Add a POST endpoint to this.")
        for msg in messages:
            print(msg.text())

        # You can also fork via the client directly.
        print("\n--- Forking via client.fork_session() ---")
        second_fork = await client.fork_session(session.session_id)
        print(f"Second fork from original: {second_fork.session_id}")


if __name__ == "__main__":
    asyncio.run(main())
