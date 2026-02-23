# /// script
# requires-python = ">=3.12"
# dependencies = ["conduit-agent-sdk"]
# ///
"""02 — Registry Browse: Fetch the ACP registry and explore available agents.

Demonstrates fetching the registry, listing all agents, searching by
keyword, and resolving an agent to a shell command.

    uv run examples/02_registry_browse.py
"""

import asyncio

from conduit_sdk import Registry


async def main():
    registry = Registry()
    await registry.fetch()

    # List every agent in the registry.
    agents = await registry.list_agents()
    print(f"Registry contains {len(agents)} agents:\n")
    for agent in agents:
        print(f"  {agent.id:20s}  {agent.name:25s}  v{agent.version}")

    # Search by keyword.
    print("\n--- Search: 'claude' ---")
    for agent in registry.search("claude"):
        print(f"  {agent.id}: {agent.description}")

    # Resolve an agent to a command (shows what would be executed).
    print("\n--- Resolve: 'claude-acp' ---")
    try:
        cmd, env = await registry.resolve_command("claude-acp")
        print(f"  Command: {cmd}")
        if env:
            print(f"  Env: {env}")
    except Exception as exc:
        print(f"  Could not resolve: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
