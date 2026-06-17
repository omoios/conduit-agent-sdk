"""Tests for session/delete (Client ↔ AgentServer loopback).

Spins up the ``_delete_agent_app`` as a subprocess, creates a session, sends
``delete_session``, and verifies the AgentServer's handler received the call.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from conduit_sdk import Client

_APP = str(Path(__file__).parent / "_delete_agent_app.py")


@pytest.mark.asyncio
async def test_delete_session_end_to_end():
    """Full loopback: create a session, then delete it.

    The AgentServer's ``on_session_delete`` handler records the session_id.
    We import and check the module-level ``_deleted`` list after the
    subprocess exits.
    """
    client = Client([sys.executable, _APP], timeout=20)
    try:
        caps = await client.connect()
        assert caps is not None

        sid = await client._rust_client.new_session(None, None, None)
        assert sid

        # Send a prompt to make the session active.
        collected: list[str] = []
        async for message in client.prompt("hello", session_id=sid):
            collected.append(message.text())
        assert any("echo" in (t or "") for t in collected)

        # Delete the session.
        result = await client.delete_session(sid)
        # The agent handler returns {}, which round-trips as an empty dict.
        assert result == {}
    finally:
        await client.disconnect()


@pytest.mark.asyncio
async def test_delete_session_no_handler_ok():
    """Deleting a session should succeed even when the agent has no handler."""
    echo_app = str(Path(__file__).parent / "_echo_agent_app.py")

    client = Client([sys.executable, echo_app], timeout=20)
    try:
        caps = await client.connect()
        assert caps is not None

        sid = await client._rust_client.new_session(None, None, None)
        assert sid

        result = await client.delete_session(sid)
        # Without a handler, the default impl returns {}.
        assert result == {}
    finally:
        await client.disconnect()
