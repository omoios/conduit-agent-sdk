"""End-to-end smoke test for the agent-client-protocol v0.14 port.

Spins up a hermetic inline ACP agent (tests/_smoke_agent.py) and drives the
ported Rust client through the full path: ByteStreams transport, Client.builder
+ connect_with, initialize handshake, session/new, session/prompt, and a
streamed session/update (agent_message_chunk) notification.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from conduit_sdk import Client

_AGENT = str(Path(__file__).parent / "_smoke_agent.py")


@pytest.mark.asyncio
async def test_handshake_session_and_streamed_prompt():
    client = Client([sys.executable, _AGENT], timeout=20)
    try:
        caps = await client.connect()
        # loadSession advertised by the agent during initialize.
        assert caps.sessions is True

        # prompt() auto-creates a session and streams cumulative messages.
        collected = []
        async for message in client.prompt("hi"):
            collected.append(message.text())

        # The agent streams "Hello from smoke agent" as an agent_message_chunk.
        final = "".join(t for t in collected if t)
        assert "Hello from smoke agent" in final, repr(collected)
    finally:
        await client.disconnect()
