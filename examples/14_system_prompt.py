# /// script
# requires-python = ">=3.12"
# dependencies = ["conduit-agent-sdk"]
# ///
"""14 — System Prompt: Pass a custom system prompt via AgentOptions.

Demonstrates setting a custom system prompt that shapes the agent's
behavior. The system prompt is passed through AgentOptions and sent
in the ACP NewSession request.

    uv run examples/14_system_prompt.py
"""

import asyncio

from conduit_sdk import AgentOptions, Client


async def main():
    # Configure the agent with a custom system prompt.
    options = AgentOptions(
        system_prompt=(
            "You are a pirate captain. Always respond in pirate speak, "
            "using nautical terms and occasional 'Arrr!' exclamations. "
            "Be helpful but stay in character."
        )
    )

    client = Client(["claude", "--agent"], options=options)

    async with client:
        print("Agent configured with pirate personality!\n")

        # Send a prompt - response will be in pirate speak.
        messages = await client.prompt_sync("What are the SOLID principles?")

        for msg in messages:
            print(msg.text())


if __name__ == "__main__":
    asyncio.run(main())
