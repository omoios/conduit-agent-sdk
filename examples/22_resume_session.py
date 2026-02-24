# /// script
# requires-python = ">=3.12"
# dependencies = ["conduit-agent-sdk"]
# ///
"""22 — Resume Session: Resume a previous conversation session.

Demonstrates using resume_session() to continue a conversation from
a previous session. This is useful for maintaining context across
multiple runs of your application.

    uv run examples/22_resume_session.py
"""

import asyncio

from conduit_sdk import Client


async def main():
    client = Client(["claude", "--agent"])

    async with client:
        # Create a session and establish some context.
        print("--- Creating initial session ---")
        session = await client.new_session()
        session_id = session.session_id
        print(f"Session ID: {session_id}\n")

        # Have a conversation.
        print("--- Initial conversation ---")
        messages = await session.prompt(
            "My name is Alice. Remember that for future reference."
        )
        for msg in messages:
            print(msg.text())

        messages = await session.prompt(
            "I'm working on a Python project that processes CSV files."
        )
        for msg in messages:
            print(msg.text())

        print(f"\n--- Session ID to resume: {session_id} ---")

        # Simulate disconnecting and reconnecting by using resume_session.
        print("\n--- Resuming session ---")
        resumed_session = await client.resume_session(session_id)
        print(f"Resumed session: {resumed_session.session_id}")
        print(f"Session ID matches: {resumed_session.session_id == session_id}\n")

        # The resumed session has the full conversation history.
        print("--- Continuing conversation (agent remembers context) ---")
        messages = await resumed_session.prompt(
            "What's my name and what am I working on?"
        )
        for msg in messages:
            print(msg.text())

        # You can also specify a working directory when resuming.
        print("\n--- Resuming with specific cwd ---")
        resumed_session2 = await client.resume_session(session_id, cwd=".")
        print(f"Resumed with cwd: {resumed_session2.session_id}")


if __name__ == "__main__":
    asyncio.run(main())
