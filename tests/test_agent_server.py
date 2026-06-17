"""Tests for conduit_sdk.agent (AgentServer).

Includes an end-to-end client<->agent loopback: conduit's Rust-backed Client
spawns a conduit AgentServer subprocess and drives a full turn, proving the
agent side interoperates with a real ACP client over the wire protocol.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from conduit_sdk import Client
from conduit_sdk.agent import AgentServer

_APP = str(Path(__file__).parent / "_echo_agent_app.py")


class _FakeTransport:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def write(self, msg: dict) -> None:
        self.sent.append(msg)

    def results(self) -> list[dict]:
        return [m for m in self.sent if "result" in m]

    def errors(self) -> list[dict]:
        return [m for m in self.sent if "error" in m]

    def updates(self) -> list[dict]:
        return [m for m in self.sent if m.get("method") == "session/update"]


# --- isolated dispatch (no subprocess) -------------------------------------


@pytest.mark.asyncio
async def test_initialize_default_response():
    server = AgentServer(name="t", version="1.2.3")
    t = _FakeTransport()
    await server._handle(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": 1}},
        t.write,
    )
    assert len(t.results()) == 1
    r = t.results()[0]["result"]
    assert r["protocolVersion"] == 1
    assert r["agentInfo"] == {"name": "t", "version": "1.2.3"}
    assert r["agentCapabilities"]["loadSession"] is False


@pytest.mark.asyncio
async def test_new_session_default_uuid():
    server = AgentServer()
    t = _FakeTransport()
    await server._handle(
        {"jsonrpc": "2.0", "id": 2, "method": "session/new", "params": {"cwd": "/tmp"}},
        t.write,
    )
    sid = t.results()[0]["result"]["sessionId"]
    assert isinstance(sid, str) and sid


@pytest.mark.asyncio
async def test_new_session_custom_handler():
    server = AgentServer()

    @server.on_new_session
    async def new_session(params):
        return "custom-sid"

    t = _FakeTransport()
    await server._handle(
        {"jsonrpc": "2.0", "id": 1, "method": "session/new", "params": {}}, t.write
    )
    assert t.results()[0]["result"]["sessionId"] == "custom-sid"


@pytest.mark.asyncio
async def test_prompt_streams_chunks_and_stop_reason():
    server = AgentServer()

    @server.on_prompt
    async def prompt(ctx, session_id, content):
        await ctx.send_text("hi")
        await ctx.send_thought("thinking")
        return "end_turn"

    t = _FakeTransport()
    await server._handle(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "session/prompt",
            "params": {"sessionId": "s1", "prompt": [{"type": "text", "text": "x"}]},
        },
        t.write,
    )
    assert len(t.updates()) == 2
    assert t.updates()[0]["params"]["update"]["sessionUpdate"] == "agent_message_chunk"
    assert t.updates()[0]["params"]["update"]["content"]["text"] == "hi"
    assert t.updates()[1]["params"]["update"]["sessionUpdate"] == "agent_thought_chunk"
    assert t.results()[0]["result"]["stopReason"] == "end_turn"


@pytest.mark.asyncio
async def test_prompt_defaults_to_end_turn_when_none():
    server = AgentServer()

    @server.on_prompt
    async def prompt(ctx, session_id, content):
        return None

    t = _FakeTransport()
    await server._handle(
        {"jsonrpc": "2.0", "id": 1, "method": "session/prompt", "params": {"sessionId": "s", "prompt": []}},
        t.write,
    )
    assert t.results()[0]["result"]["stopReason"] == "end_turn"


@pytest.mark.asyncio
async def test_prompt_invalid_stop_reason_is_error():
    server = AgentServer()

    @server.on_prompt
    async def prompt(ctx, session_id, content):
        return "bogus"

    t = _FakeTransport()
    await server._handle(
        {"jsonrpc": "2.0", "id": 4, "method": "session/prompt", "params": {"sessionId": "s", "prompt": []}},
        t.write,
    )
    assert len(t.errors()) == 1
    assert t.errors()[0]["error"]["code"] == -32603


@pytest.mark.asyncio
async def test_load_session_advertised_when_registered():
    server = AgentServer()

    @server.on_session_load
    async def load(params):
        return {}

    t = _FakeTransport()
    await server._handle(
        {"jsonrpc": "2.0", "id": 5, "method": "initialize", "params": {"protocolVersion": 1}},
        t.write,
    )
    assert t.results()[0]["result"]["agentCapabilities"]["loadSession"] is True


@pytest.mark.asyncio
async def test_unknown_method_is_method_not_found():
    server = AgentServer()
    t = _FakeTransport()
    await server._handle(
        {"jsonrpc": "2.0", "id": 6, "method": "session/setName", "params": {}}, t.write
    )
    assert t.errors()[0]["error"]["code"] == -32601


@pytest.mark.asyncio
async def test_cancel_notification_has_no_response():
    server = AgentServer()
    cancelled: list[str] = []

    @server.on_cancel
    async def cancel(params):
        cancelled.append(params.get("sessionId"))

    t = _FakeTransport()
    await server._handle(
        {"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": "s"}}, t.write
    )
    assert cancelled == ["s"]
    assert t.results() == [] and t.errors() == []


# --- end-to-end: conduit Client <-> conduit AgentServer --------------------


@pytest.mark.asyncio
async def test_client_to_conduit_agent_loopback():
    """The Rust-backed Client spawns a conduit AgentServer subprocess and
    drives a full turn over the real ACP wire protocol."""
    client = Client([sys.executable, _APP], timeout=20)
    try:
        caps = await client.connect()
        assert caps is not None  # initialize handshake completed

        collected: list[str] = []
        async for message in client.prompt("hello world"):
            collected.append(message.text())

        full = "".join(s for s in collected if s)
        assert "echo: hello world" in full, repr(collected)
        assert "(done)" in full, repr(collected)
    finally:
        await client.disconnect()
