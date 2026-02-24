# /// script
# requires-python = ">=3.12"
# dependencies = ["conduit-agent-sdk"]
# ///
"""18 — Config Options: Set configuration options on a session.

Demonstrates using set_config() to change agent configuration options
mid-session. Options can include thinking mode, auto-approve settings,
and other agent-specific configurations.

    uv run examples/18_config_options.py
"""

import asyncio

from conduit_sdk import Client


async def main():
    client = Client(["claude", "--agent"])

    async with client:
        # Create a session.
        session = await client.new_session()
        print(f"Session created: {session.session_id}\n")

        # Set configuration options on the session.
        # Enable thinking mode to see the agent's reasoning.
        result = await session.set_config("thinking", "enabled")
        print(f"Set 'thinking' to 'enabled': {result}\n")

        # Send a prompt - thinking blocks may be included in response.
        print("Prompting with thinking enabled...")
        messages = await session.prompt("Explain how quicksort works in one paragraph.")

        for msg in messages:
            print(msg.text())

        # You can also set config via the client directly.
        print("\n--- Setting config via client ---")
        result = await client.set_config(session.session_id, "thinking", "disabled")
        print(f"Set 'thinking' to 'disabled': {result}\n")

        messages = await session.prompt("Now summarize bubble sort briefly.")
        for msg in messages:
            print(msg.text())


if __name__ == "__main__":
    asyncio.run(main())
