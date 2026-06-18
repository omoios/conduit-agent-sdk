"""End-to-end: observe an agent's tool-call outputs over the real ACP wire.

Drives a real Client against the rich-agent fixture (a subprocess speaking ACP
over stdio) and asserts that toolview.observe_turn surfaces the tool call's
name, parsed input, and decoded output — proving the observability works on
live protocol traffic, not just synthetic SessionUpdate objects.
"""

import os
import sys

import pytest

from conduit_sdk import Client
from conduit_sdk.toolview import observe_turn

HERE = os.path.dirname(os.path.abspath(__file__))
RICH_AGENT = [sys.executable, os.path.join(HERE, "_rich_agent_app.py")]


@pytest.mark.asyncio
async def test_observe_turn_surfaces_tool_call_over_the_wire():
    async with Client(RICH_AGENT, timeout=15) as client:
        await client.new_session()
        turn = await observe_turn(client, "read the config and tell me the port")

    # Final assistant text is captured.
    assert "port 8080" in turn.text

    # Exactly one tool call, fully decoded from the wire.
    assert len(turn.tool_calls) == 1
    tc = turn.tool_calls[0]
    assert tc.name == "Read config.txt"
    assert tc.input == {"path": "config.txt"}
    assert tc.output == "port = 8080"
    assert tc.status is not None
