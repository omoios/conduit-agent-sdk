# /// script
# requires-python = ">=3.12"
# dependencies = ["conduit-agent-sdk"]
# ///
"""16 — Max Turns: Limit conversation turns via AgentOptions.

Demonstrates setting a maximum turn limit on the conversation.
This is useful for controlling costs and preventing runaway
conversations in automated scenarios.

    uv run examples/16_max_turns.py
"""

import asyncio

from conduit_sdk import AgentOptions, Client


async def main():
    # Configure the agent with a low turn limit for demonstration.
    options = AgentOptions(
        max_turns=3, system_prompt="You are a helpful coding assistant."
    )

    client = Client(["claude", "--agent"], options=options)

    async with client:
        # Create a session for multi-turn conversation.
        session = await client.new_session()
        print(f"Session created with max_turns=3: {session.session_id}\n")

        # First turn.
        print("--- Turn 1 ---")
        messages = await session.prompt("What is Python's GIL?")
        for msg in messages:
            print(msg.text())
            if msg.stop_reason:
                print(f"[stop_reason: {msg.stop_reason}]")

        # Second turn.
        print("\n--- Turn 2 ---")
        messages = await session.prompt("How does it affect multi-threading?")
        for msg in messages:
            print(msg.text())
            if msg.stop_reason:
                print(f"[stop_reason: {msg.stop_reason}]")

        # Third turn - may hit the limit.
        print("\n--- Turn 3 ---")
        messages = await session.prompt("What are alternatives to avoid GIL issues?")
        for msg in messages:
            print(msg.text())
            if msg.stop_reason:
                print(f"[stop_reason: {msg.stop_reason}]")


if __name__ == "__main__":
    asyncio.run(main())
