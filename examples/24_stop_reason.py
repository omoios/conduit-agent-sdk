# /// script
# requires-python = ">=3.12"
# dependencies = ["conduit-agent-sdk"]
# ///
"""24 — Stop Reason: Check stop_reason on response messages.

Demonstrates that Message objects carry a stop_reason field
(e.g. 'end_turn', 'max_tokens', 'cancelled', 'refusal').

    uv run examples/24_stop_reason.py
"""

import asyncio

from conduit_sdk import Client


async def main():
    client = Client(["claude", "--agent"])

    async with client:
        print("Sending prompt and checking stop_reason...\n")

        messages = await client.prompt_sync("Say hello in one word.")

        for msg in messages:
            print(f"Text: {msg.text()}")
            print(f"Stop reason: {msg.stop_reason}")


if __name__ == "__main__":
    asyncio.run(main())
