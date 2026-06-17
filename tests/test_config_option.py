"""Tests for session/set_config_option (Client → AgentServer round-trip).

Verifies that the ``set_config`` method is properly wired from Python through
Rust to the wire, and that the AgentServer can receive and respond to
config-option requests.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from conduit_sdk import Client, ProtocolError

_APP = str(Path(__file__).parent / "_config_agent_app.py")


@pytest.mark.asyncio
async def test_set_config_option_sends_correctly():
    """The Python -> Rust -> wire path for set_config is wired correctly.

    The config_agent_app does NOT register a handler for
    session/set_config_option, so the AgentServer responds with
    method-not-found.  We catch the ``ProtocolError`` to confirm the
    full round-trip completed without crashes or hangs.
    """
    from conduit_sdk.exceptions import ProtocolError as PE

    client = Client([sys.executable, _APP], timeout=20)
    try:
        caps = await client.connect()
        assert caps is not None

        sid = await client._rust_client.new_session(None, None, None)
        assert sid

        with pytest.raises(PE, match="method not found"):
            await client.set_config(sid, "model", "claude-sonnet-4")
    finally:
        await client.disconnect()
