"""End-to-end loopback: client -> AgentServer -> SDK @tool (over MCP).

Proves the MCP step is genuinely complete: the client configures an SDK
McpSdkServer (in-process), the spawned AgentServer receives its http MCP
config at session/new, and the agent actually CALLS the @tool over HTTP and
streams the result back to the client.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from conduit_sdk import AgentOptions, Client, create_sdk_mcp_server, tool

_APP = str(Path(__file__).parent / "_mcp_agent_app.py")


@pytest.mark.asyncio
async def test_agent_calls_sdk_tool_over_mcp():
    @tool(description="Add two integers")
    async def add(a: int, b: int) -> int:
        return a + b

    mcp = create_sdk_mcp_server("math", tools=[add])
    options = AgentOptions(mcp_servers={"math": mcp})
    client = Client([sys.executable, _APP], options=options, timeout=30)
    try:
        await client.connect()
        collected: list[str] = []
        async for message in client.prompt("add 2 3"):
            collected.append(message.text())
        assert "5" in "".join(t for t in collected if t), repr(collected)
    finally:
        await client.disconnect()
