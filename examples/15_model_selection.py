# /// script
# requires-python = ">=3.12"
# dependencies = ["conduit-agent-sdk"]
# ///
"""15 — Model Selection: Choose a specific model via AgentOptions.

Demonstrates selecting a specific model (e.g., claude-sonnet-4) through
the AgentOptions configuration. This is useful when you want to target
a particular model for cost, speed, or capability reasons.

    uv run examples/15_model_selection.py
"""

import asyncio

from conduit_sdk import AgentOptions, Client


async def main():
    # Configure the agent to use a specific model.
    options = AgentOptions(
        model="claude-sonnet-4-20250514",
        system_prompt="You are a concise assistant. Keep responses brief.",
    )

    client = Client(["claude", "--agent"], options=options)

    async with client:
        print("Using model: claude-sonnet-4-20250514\n")
        print(f"AgentOptions._meta includes model: {options.to_meta_json()}\n")

        # The prompt will be processed by the specified model.
        messages = await client.prompt_sync(
            "Briefly explain what ACP (Agent Client Protocol) is."
        )

        for msg in messages:
            print(msg.text())


if __name__ == "__main__":
    asyncio.run(main())
