# /// script
# requires-python = ">=3.12"
# dependencies = ["conduit-agent-sdk"]
# ///
"""27 — Rich Content: Send multi-modal prompts with text, images, and resources.

Demonstrates sending structured content blocks instead of plain text:
- TextBlock for formatted text
- ImageBlock for base64-encoded images
- ResourceLinkBlock for referencing external resources
- Mixing multiple content types in a single prompt

    uv run examples/27_rich_content.py
"""

import asyncio
import base64

from conduit_sdk import (
    Client,
    ImageBlock,
    ResourceLinkBlock,
    TextBlock,
)


async def main():
    # --- Example 1: Plain text still works as before ---
    print("=== Example 1: Plain text prompt (backward compatible) ===")
    async with Client(["claude", "--agent"]) as client:
        messages = await client.prompt_sync("What is 2 + 2?")
        for msg in messages:
            print(f"Response: {msg.text()}")

    # --- Example 2: List of content blocks ---
    print("\n=== Example 2: Mixed content blocks ===")
    async with Client(["claude", "--agent"]) as client:
        # Send a prompt with multiple content blocks
        content = [
            TextBlock(text="Please analyze the following:"),
            TextBlock(text="The sky is blue because of Rayleigh scattering."),
            ResourceLinkBlock(
                uri="https://en.wikipedia.org/wiki/Rayleigh_scattering",
                name="Rayleigh Scattering",
                description="Wikipedia article on the phenomenon",
            ),
        ]
        messages = await client.prompt_sync(content)
        for msg in messages:
            print(f"Response: {msg.text()[:200]}...")

    # --- Example 3: Image content block ---
    print("\n=== Example 3: Image content block ===")
    # Create a tiny 1x1 red PNG for demonstration
    tiny_png = base64.b64encode(
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
        b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00"
        b"\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00"
        b"\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    ).decode()

    content_with_image = [
        TextBlock(text="What do you see in this image?"),
        ImageBlock(data=tiny_png, mime_type="image/png"),
    ]

    async with Client(["claude", "--agent"]) as client:
        messages = await client.prompt_sync(content_with_image)
        for msg in messages:
            print(f"Response: {msg.text()[:200]}...")

    # --- Example 4: Strings in content list (auto-wrapped as text) ---
    print("\n=== Example 4: Strings auto-wrapped as text blocks ===")
    async with Client(["claude", "--agent"]) as client:
        content = [
            "First paragraph: explain gravity.",
            "Second paragraph: explain magnetism.",
        ]
        messages = await client.prompt_sync(content)
        for msg in messages:
            print(f"Response: {msg.text()[:200]}...")


if __name__ == "__main__":
    asyncio.run(main())
