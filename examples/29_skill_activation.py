# /// script
# requires-python = ">=3.12"
# dependencies = ["conduit-agent-sdk"]
# ///
"""29 — Skill Activation: Discover and invoke agent slash commands (skills).

Demonstrates how to:
1. Discover available commands via CommandsUpdate streaming events
2. Activate a slash command by sending it as a prompt
3. Collect the response from a skill invocation

In ACP, slash commands (skills) are activated by prompting with the command
text. The agent advertises available commands via AvailableCommandsUpdate
notifications during streaming.

    uv run examples/29_skill_activation.py
"""

import asyncio

from conduit_sdk import Client
from conduit_sdk.events import AvailableCommands, TextDelta, Done


async def discover_commands(client: Client) -> list[dict]:
    """Send a prompt and capture available commands from streaming events.

    The agent sends CommandsUpdate notifications during session setup and
    prompting. These contain the full list of available slash commands.
    """
    commands: list[dict] = []

    async for event in client.prompt_stream("What commands do you have?"):
        if isinstance(event, AvailableCommands) and event.commands:
            commands = event.commands
        elif isinstance(event, TextDelta):
            pass  # Ignore text for discovery
        elif isinstance(event, Done):
            break

    return commands


async def activate_skill(client: Client, command: str) -> str:
    """Activate a slash command and collect the full text response.

    Slash commands are invoked by sending them as prompt text. The agent
    processes the command and streams back the response.
    """
    collected: list[str] = []

    async for event in client.prompt_stream(command):
        if isinstance(event, TextDelta):
            collected.append(event.text or "")
        elif isinstance(event, Done):
            break

    return "".join(collected)


async def main() -> None:
    client = Client(["opencode", "acp"])

    async with client:
        # --- Step 1: Discover available commands ---
        print("Discovering available commands...\n")
        commands = await discover_commands(client)

        if commands:
            print(f"Found {len(commands)} commands:")
            for cmd in commands[:10]:  # Show first 10
                name = cmd.get("name", cmd.get("id", "unknown"))
                desc = cmd.get("description", "")
                print(f"  {name}: {desc[:60]}")
            if len(commands) > 10:
                print(f"  ... and {len(commands) - 10} more")
        else:
            print("No commands received via CommandsUpdate.")
            print("(The agent may not advertise commands this way.)")

        # --- Step 2: Activate a skill ---
        print("\n--- Activating /help ---\n")
        result = activate_skill(client, "/help")
        help_text = await result

        if help_text:
            # Print first 500 chars of the help output
            preview = help_text[:500]
            if len(help_text) > 500:
                preview += "..."
            print(help_text)
        else:
            print("No text response from /help.")

        print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
