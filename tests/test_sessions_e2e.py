"""End-to-end tests for session management (Client ↔ AgentServer loopback).

Spins up the ``_session_echo_agent_app`` as a subprocess and exercises
multi-session creation, per-session routing, and session deletion.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from conduit_sdk import Client

_APP = str(Path(__file__).parent / "_session_echo_agent_app.py")


@pytest.mark.asyncio
async def test_two_sessions_distinct():
    """Create two sessions and verify they have distinct, truthy IDs."""
    client = Client([sys.executable, _APP], timeout=15)
    try:
        caps = await client.connect()
        assert caps is not None

        s1 = await client.new_session()
        s2 = await client.new_session()

        assert s1.session_id, "first session id should be truthy"
        assert s2.session_id, "second session id should be truthy"
        assert s1.session_id != s2.session_id, "two sessions must have distinct ids"
    finally:
        await client.disconnect()


@pytest.mark.asyncio
async def test_per_session_routing():
    """Prompt within each session and verify the reply echoes that session's id."""
    client = Client([sys.executable, _APP], timeout=15)
    try:
        caps = await client.connect()
        assert caps is not None

        s1 = await client.new_session()
        s2 = await client.new_session()

        # Prompt within each session.
        msgs1 = await client.prompt_sync("hello", session_id=s1.session_id)
        msgs2 = await client.prompt_sync("hello", session_id=s2.session_id)

        reply1 = "".join(m.text() for m in msgs1 if m.text())
        reply2 = "".join(m.text() for m in msgs2 if m.text())

        assert s1.session_id in reply1, (
            f"reply for session1 should contain its id; reply={reply1!r}"
        )
        assert s2.session_id in reply2, (
            f"reply for session2 should contain its id; reply={reply2!r}"
        )
    finally:
        await client.disconnect()


@pytest.mark.asyncio
async def test_delete_session_roundtrip():
    """Delete a session and verify the agent handler returns {}."""
    client = Client([sys.executable, _APP], timeout=15)
    try:
        caps = await client.connect()
        assert caps is not None

        s1 = await client.new_session()
        sid = s1.session_id
        assert sid

        # Send a prompt to make the session active (mirrors test_session_delete.py).
        msgs = await client.prompt_sync("hello", session_id=sid)
        assert any(m.text() for m in msgs)

        # Delete the session.
        result = await client.delete_session(sid)
        assert result == {}
    finally:
        await client.disconnect()
