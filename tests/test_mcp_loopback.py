"""End-to-end loopback: client -> AgentServer -> SDK @tool (over MCP).

Proves the MCP step is genuinely complete: the client configures an SDK
McpSdkServer (in-process), the spawned AgentServer receives its http MCP
config at session/new, and the agent actually CALLS the @tool over HTTP and
streams the result back to the client.
"""
from __future__ import annotations

import json
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


_APP_MULTI = str(Path(__file__).parent / "_mcp_multi_agent_app.py")


@pytest.mark.asyncio
async def test_agent_calls_multiple_sdk_tools_across_servers():
    """Multi-server aggregation (restores the dropped handle_mcp_request coverage):
    two SDK MCP servers are exposed to the agent at once and both are called in
    a single turn.
    """
    @tool(description="Add two integers")
    async def add(a: int, b: int) -> int:
        return a + b

    @tool(description="Uppercase a string")
    async def upper(s: str) -> str:
        return s.upper()

    math_mcp = create_sdk_mcp_server("math", tools=[add])
    text_mcp = create_sdk_mcp_server("text", tools=[upper])
    options = AgentOptions(mcp_servers={"math": math_mcp, "text": text_mcp})

    # Both SDK servers are started on connect and both http configs are emitted.
    client = Client([sys.executable, _APP_MULTI], options=options, timeout=30)
    try:
        await client.connect()
        configs = json.loads(options.to_mcp_servers_json())
        assert {c["name"] for c in configs} == {"math", "text"}

        collected: list[str] = []
        async for message in client.prompt("calc 2 3 word hi"):
            collected.append(message.text())
        out = "".join(t for t in collected if t)
        # math.add(2,3) -> 5  AND  text.upper("hi") -> HI, both in one turn.
        assert "5" in out and "HI" in out, repr(collected)
    finally:
        await client.disconnect()
