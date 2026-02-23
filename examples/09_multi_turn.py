# /// script
# requires-python = ">=3.12"
# dependencies = ["conduit-agent-sdk"]
# ///
"""09 — Multi-Turn: Multi-turn conversation with session management.

Demonstrates creating a session, sending multiple prompts, and
changing the mode/model mid-conversation.

    uv run examples/09_multi_turn.py
"""

import asyncio

from conduit_sdk import Client


async def main():
    client = await Client.from_registry("claude-acp")

    async with client:
        # Create a session for multi-turn conversation.
        session = await client.new_session()
        print(f"Session created: {session.session_id}\n")

        # First turn.
        print("--- Turn 1 ---")
        response = await session.prompt("What are the SOLID principles?")
        for msg in response:
            print(msg.text())

        # Change configuration mid-conversation.
        await session.set_mode("code")
        print("\n--- Turn 2 (code mode) ---")
        response = await session.prompt(
            "Show me a Python example that violates the Single Responsibility Principle, "
            "then refactor it."
        )
        for msg in response:
            print(msg.text())

        # Third turn — builds on previous context.
        print("\n--- Turn 3 ---")
        response = await session.prompt("Now add type hints to the refactored version.")
        for msg in response:
            print(msg.text())


if __name__ == "__main__":
    asyncio.run(main())
