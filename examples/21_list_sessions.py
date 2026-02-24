# /// script
# requires-python = ">=3.12"
# dependencies = ["conduit-agent-sdk"]
# ///
"""21 — List Sessions: List available sessions from the agent.

Demonstrates using list_sessions() to retrieve information about
available sessions. This is useful for session management and
resuming previous conversations.

    uv run examples/21_list_sessions.py
"""

import asyncio

from conduit_sdk import Client


async def main():
    client = Client(["claude", "--agent"])

    async with client:
        # List sessions before creating any.
        print("--- Sessions before creation ---")
        sessions = await client.list_sessions()
        print(f"Found {len(sessions)} sessions")
        for s in sessions:
            print(f"  - {s}")

        # Create a few sessions.
        print("\n--- Creating sessions ---")
        session1 = await client.new_session()
        await session1.prompt("What is 2 + 2?")
        print(f"Session 1: {session1.session_id}")

        session2 = await client.new_session()
        await session2.prompt("What is the capital of France?")
        print(f"Session 2: {session2.session_id}")

        session3 = await client.new_session()
        await session3.prompt("Name a programming language.")
        print(f"Session 3: {session3.session_id}")

        # List sessions after creation.
        print("\n--- Sessions after creation ---")
        sessions = await client.list_sessions()
        print(f"Found {len(sessions)} sessions")

        for s in sessions:
            # Each session dict typically contains: session_id, title, created_at, etc.
            session_id = s.get("session_id", s.get("id", "unknown"))
            title = s.get("title", "Untitled")
            print(f"  - {session_id}: {title}")

        # You can also filter by working directory.
        print("\n--- Listing with cwd filter ---")
        sessions = await client.list_sessions(cwd=".")
        print(f"Sessions in current directory: {len(sessions)}")


if __name__ == "__main__":
    asyncio.run(main())
