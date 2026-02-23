# /// script
# requires-python = ">=3.12"
# dependencies = ["conduit-agent-sdk"]
# ///
"""01 — Hello World: The simplest possible agent interaction.

Send a single prompt to an ACP agent and print the response.
Uses the top-level ``query()`` function for maximum convenience.

    uv run examples/01_hello_world.py
"""

import asyncio

from conduit_sdk import query


async def main():
    async for message in query(prompt="What is ACP?", agent="claude-acp"):
        print(message.text())


if __name__ == "__main__":
    asyncio.run(main())
