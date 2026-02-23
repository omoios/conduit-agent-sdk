# /// script
# requires-python = ">=3.12"
# dependencies = ["conduit-agent-sdk"]
# ///
"""03 — Streaming: Async iteration over response messages.

Uses ``Client.from_registry()`` for registry-based agent resolution
and streams responses as they arrive.

    uv run examples/03_streaming.py
"""

import asyncio

from conduit_sdk import Client


async def main():
    client = await Client.from_registry("claude-acp")

    async with client:
        print(f"Connected! Capabilities: {client.capabilities}\n")

        # Stream responses as they arrive.
        async for message in client.prompt("Explain ACP in 3 sentences."):
            print(message.text(), end="", flush=True)
        print()  # trailing newline


if __name__ == "__main__":
    asyncio.run(main())
