# /// script
# requires-python = ">=3.12"
# dependencies = ["conduit-agent-sdk"]
# ///
"""08 — Code Generation: Ask the agent to write a Python module to disk.

Demonstrates using an ACP agent to generate code. The agent uses
its Write tool to create a file.

    uv run examples/08_code_generation.py
"""

import asyncio

from conduit_sdk import AgentOptions, Client


async def main():
    client = await Client.from_registry(
        "claude-acp",
        options=AgentOptions(
            # Allow the agent to write files.
            allowed_tools=["Read", "Write", "Glob"],
            max_turns=5,
        ),
    )

    async with client:
        async for msg in client.prompt(
            "Write a Python module at /tmp/fizzbuzz.py that implements "
            "a fizzbuzz function and includes a __main__ block."
        ):
            print(msg.text())


if __name__ == "__main__":
    asyncio.run(main())
