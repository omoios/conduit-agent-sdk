# /// script
# requires-python = ">=3.12"
# dependencies = ["conduit-agent-sdk"]
# ///
"""26 — Agent Info: Read agent server information.

Demonstrates using the agent_info async property to retrieve metadata
about the connected agent, including its name, version, and capabilities.
This is useful for adapting your code to different agents.

    uv run examples/26_agent_info.py
"""

import asyncio

from conduit_sdk import Client


async def main():
    client = Client(["claude", "--agent"])

    async with client:
        # agent_info is an async property - await it like a coroutine.
        info = await client.agent_info

        print("--- Agent Server Info ---")
        if info is None:
            print("No agent info available (agent may not support this)")
        else:
            print(f"Raw info: {info}\n")

            # Common fields in agent info.
            name = info.get("name", "Unknown")
            version = info.get("version", "Unknown")
            title = info.get("title", name)

            print(f"Name: {name}")
            print(f"Title: {title}")
            print(f"Version: {version}")

            # Capabilities if available.
            if "capabilities" in info:
                print(f"\nCapabilities:")
                caps = info["capabilities"]
                if isinstance(caps, dict):
                    for key, value in caps.items():
                        print(f"  - {key}: {value}")
                elif isinstance(caps, list):
                    for cap in caps:
                        print(f"  - {cap}")

            # Supported modes if available.
            if "modes" in info:
                print(f"\nSupported modes:")
                for mode in info["modes"]:
                    if isinstance(mode, dict):
                        mode_id = mode.get("id", "unknown")
                        mode_name = mode.get("name", mode_id)
                        print(f"  - {mode_id}: {mode_name}")
                    else:
                        print(f"  - {mode}")

            # Other metadata.
            other_keys = set(info.keys()) - {
                "name",
                "version",
                "title",
                "capabilities",
                "modes",
            }
            if other_keys:
                print(f"\nAdditional metadata:")
                for key in sorted(other_keys):
                    print(f"  - {key}: {info[key]}")

        # Also compare with client.capabilities (sync property).
        print("\n--- Client Capabilities ---")
        print(f"Capabilities: {client.capabilities}")


if __name__ == "__main__":
    asyncio.run(main())
