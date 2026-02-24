# /// script
# requires-python = ">=3.12"
# dependencies = ["conduit-agent-sdk"]
# ///
"""27 — Rich Content: Send multi-modal prompts with text, images, and resources.

Demonstrates sending structured content blocks instead of plain text.
Uses a single registry-resolved agent connection for all examples.

    .venv/bin/python examples/27_rich_content.py
"""

import asyncio

from conduit_sdk import (
    Client,
    Registry,
    ResourceLinkBlock,
    TextBlock,
)


async def main():
    registry = Registry()
    await registry.fetch()
    client = await Client.from_registry("claude-acp", registry=registry, timeout=60)

    async with client:
        print("=== Example 1: Plain text (backward compatible) ===")
        messages = await client.prompt_sync("What is 2 + 2? Reply in one sentence.")
        for msg in messages:
            print(f"  {msg.text()}")

        print("\n=== Example 2: Mixed content blocks ===")
        content = [
            TextBlock(text="Summarize this topic in one sentence:"),
            TextBlock(text="Rayleigh scattering explains why the sky is blue."),
            ResourceLinkBlock(
                uri="https://en.wikipedia.org/wiki/Rayleigh_scattering",
                name="Rayleigh Scattering",
            ),
        ]
        messages = await client.prompt_sync(content)
        for msg in messages:
            print(f"  {msg.text()[:300]}")

        print("\n=== Example 3: Strings auto-wrapped as text blocks ===")
        content = [
            "Explain gravity in one sentence.",
            "Then explain magnetism in one sentence.",
        ]
        messages = await client.prompt_sync(content)
        for msg in messages:
            print(f"  {msg.text()[:300]}")

    print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
