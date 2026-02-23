# /// script
# requires-python = ">=3.12"
# dependencies = ["conduit-agent-sdk"]
# ///
"""06 — Custom Tools: Register Python functions as agent tools via MCP.

Uses the ``@tool`` decorator and ``create_sdk_mcp_server()`` to expose
Python functions that the agent can discover and invoke at runtime.

    uv run examples/06_custom_tools.py
"""

import asyncio
from pathlib import Path

from conduit_sdk import AgentOptions, Client, create_sdk_mcp_server, tool


@tool(description="Read a file from the local filesystem")
async def read_file(path: str) -> str:
    """Read and return the contents of a file."""
    return Path(path).read_text()


@tool(description="List files in a directory")
async def list_directory(path: str) -> str:
    """List files and directories at the given path."""
    entries = sorted(Path(path).iterdir())
    return "\n".join(str(e) for e in entries)


@tool(description="Query the database and return results as JSON")
async def query_db(sql: str) -> str:
    """Execute a SQL query (stub — returns sample data)."""
    return '{"rows": [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]}'


async def main():
    # Bundle tools into an MCP server that the agent can discover.
    server = create_sdk_mcp_server(
        "my-tools",
        version="1.0.0",
        tools=[read_file, list_directory, query_db],
    )

    client = await Client.from_registry(
        "claude-acp",
        options=AgentOptions(mcp_servers={"my-tools": server}),
    )

    async with client:
        async for msg in client.prompt("List the files in /tmp and read any .txt files"):
            print(msg.text())


if __name__ == "__main__":
    asyncio.run(main())
