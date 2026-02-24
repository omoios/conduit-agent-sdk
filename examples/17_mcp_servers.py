# /// script
# requires-python = ">=3.12"
# dependencies = ["conduit-agent-sdk"]
# ///
"""17 — MCP Servers: Configure MCP servers via AgentOptions.

Demonstrates passing Model Context Protocol (MCP) server configurations
through AgentOptions. MCP servers provide additional tools and resources
to the agent at runtime.

    uv run examples/17_mcp_servers.py
"""

import asyncio

from conduit_sdk import AgentOptions, Client


async def main():
    # Configure an MCP server that provides filesystem access.
    mcp_config = {
        "filesystem": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
            "env": {},
        }
    }

    options = AgentOptions(
        mcp_servers=mcp_config,
        system_prompt="You are a helpful assistant with filesystem access.",
    )

    client = Client(["claude", "--agent"], options=options)

    async with client:
        print("Configured with MCP server: filesystem")
        print(f"MCP config JSON: {options.to_mcp_servers_json()}\n")

        # Create a session - MCP servers are initialized during session creation.
        session = await client.new_session()
        print(f"Session created: {session.session_id}\n")

        # The agent can now use tools from the MCP server.
        messages = await session.prompt(
            "List the files in /tmp and tell me what you find."
        )

        for msg in messages:
            print(msg.text())


if __name__ == "__main__":
    asyncio.run(main())
