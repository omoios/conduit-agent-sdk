# /// script
# requires-python = ">=3.12"
# dependencies = ["conduit-agent-sdk"]
# ///
"""07 — File Operations: Ask the agent to read, list, and summarize files.

A practical example of using an ACP agent for file system tasks.
The agent uses its built-in tools (Read, Glob, etc.) to explore files.

    uv run examples/07_file_operations.py
"""

import asyncio

from conduit_sdk import AgentOptions, Client


async def main():
    client = await Client.from_registry(
        "claude-acp",
        options=AgentOptions(
            # Only allow read-only tools for safety.
            allowed_tools=["Read", "Glob", "Grep"],
        ),
    )

    async with client:
        async for msg in client.prompt(
            "List the Python files in the current directory and "
            "give me a one-line summary of each."
        ):
            print(msg.text())


if __name__ == "__main__":
    asyncio.run(main())
