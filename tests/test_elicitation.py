"""Tests for conduit_sdk.elicitation and the agent/client elicitation paths.

Elicitation is an UNSTABLE ACP feature: an agent sends ``elicitation/create``
to request structured user input from the client. These tests cover the
Python types, the Rust-bridge adapter, the agent-side request/response
correlation in ``AgentServer``, and a full end-to-end loopback where the
Rust-backed ``Client`` routes an elicitation request to a Python handler.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

from conduit_sdk import (
    AgentServer,
    Client,
    ElicitationRequest,
    ElicitationResponse,
    auto_accept,
    auto_decline,
)
from conduit_sdk.agent import AgentContext
from conduit_sdk.elicitation import _make_elicitation_bridge

_APP = str(Path(__file__).parent / "_elicit_agent_app.py")


async def _noop_write(_msg: dict) -> None:  # minimal stand-in transport
    return None


# --- type validation ---------------------------------------------------------


def test_response_accept_keeps_content():
    r = ElicitationResponse(action="accept", content={"x": 1})
    assert r.action == "accept"
    assert r.content == {"x": 1}


def test_response_decline_drops_content():
    r = ElicitationResponse(action="decline", content={"x": 1})
    assert r.content is None


def test_response_cancel_drops_content():
    r = ElicitationResponse(action="cancel", content={"x": 1})
    assert r.content is None


def test_response_invalid_action_raises():
    with pytest.raises(ValueError):
        ElicitationResponse(action="bogus")


# --- built-in handlers -------------------------------------------------------


@pytest.mark.asyncio
async def test_auto_handlers():
    req = ElicitationRequest(message="hi")
    assert (await auto_accept(req)).action == "accept"
    assert (await auto_decline(req)).action == "decline"


# --- bridge adapter ----------------------------------------------------------


@pytest.mark.asyncio
async def test_bridge_round_trip():
    async def handler(req: ElicitationRequest) -> ElicitationResponse:
        assert req.mode == "form"
        assert req.message == "What is your name?"
        return ElicitationResponse(action="accept", content={"name": "ada"})

    bridge = _make_elicitation_bridge(handler)
    payload = json.dumps(
        {
            "mode": "form",
            "message": "What is your name?",
            "sessionId": "s1",
            "requestedSchema": {"type": "object"},
        }
    )
    out = json.loads(await bridge(payload))
    assert out == {"action": "accept", "content": {"name": "ada"}}


@pytest.mark.asyncio
async def test_bridge_accepts_dict_return():
    async def handler(req):
        return {"action": "decline"}

    bridge = _make_elicitation_bridge(handler)
    out = json.loads(await bridge(json.dumps({"message": "x"})))
    assert out["action"] == "decline"
    assert out["content"] is None


# --- AgentContext.request_elicitation ----------------------------------------


@pytest.mark.asyncio
async def test_ctx_form_params_built_correctly():
    captured: dict = {}

    async def sender(method, params):
        captured["method"] = method
        captured["params"] = params
        return {"action": "accept", "content": {"name": "ada"}}

    ctx = AgentContext("s1", _noop_write, [], sender)
    result = await ctx.request_elicitation(
        "name?",
        requested_schema={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    )
    assert captured["method"] == "elicitation/create"
    assert captured["params"]["mode"] == "form"
    assert captured["params"]["sessionId"] == "s1"
    assert captured["params"]["requestedSchema"]["required"] == ["name"]
    assert result["content"] == {"name": "ada"}


@pytest.mark.asyncio
async def test_ctx_form_requires_schema():
    async def sender(method, params):
        return {"action": "cancel"}

    ctx = AgentContext("s1", _noop_write, [], sender)
    with pytest.raises(ValueError):
        await ctx.request_elicitation("name?", mode="form")


@pytest.mark.asyncio
async def test_ctx_url_mode_requires_url_and_id():
    ctx = AgentContext("s1", _noop_write, [], None)
    with pytest.raises(RuntimeError):
        await ctx.request_elicitation("connect", mode="url")


@pytest.mark.asyncio
async def test_ctx_url_mode_builds_params():
    captured: dict = {}

    async def sender(method, params):
        captured["params"] = params
        return {"action": "cancel"}

    ctx = AgentContext("s1", _noop_write, [], sender)
    await ctx.request_elicitation(
        "sign in", mode="url", url="https://example.org", elicitation_id="e1"
    )
    assert captured["params"]["mode"] == "url"
    assert captured["params"]["url"] == "https://example.org"
    assert captured["params"]["elicitationId"] == "e1"


# --- AgentServer request/response correlation --------------------------------


@pytest.mark.asyncio
async def test_send_request_resolves_on_response():
    server = AgentServer()
    sent: list[dict] = []

    async def write(msg):
        sent.append(msg)

    task = asyncio.create_task(
        server._send_request("elicitation/create", {"message": "hi"}, write)
    )
    await asyncio.sleep(0.01)  # let the request be written + future registered
    assert sent and sent[0]["method"] == "elicitation/create"
    req_id = sent[0]["id"]

    # Simulate the client response arriving on the read loop.
    server._pending_requests[req_id].set_result({"action": "decline"})
    result = await task
    assert result == {"action": "decline"}


@pytest.mark.asyncio
async def test_send_request_propagates_error_response():
    server = AgentServer()
    sent: list[dict] = []

    async def write(msg):
        sent.append(msg)

    task = asyncio.create_task(
        server._send_request("elicitation/create", {"message": "hi"}, write)
    )
    await asyncio.sleep(0.01)
    req_id = sent[0]["id"]
    server._pending_requests[req_id].set_exception(RuntimeError("boom"))
    with pytest.raises(RuntimeError, match="boom"):
        await task


# --- end-to-end: Client elicitation_handler <-> AgentServer ------------------


@pytest.mark.asyncio
async def test_client_elicitation_loopback():
    """Full path: the agent sends ``elicitation/create``; the Rust-backed
    Client advertises the capability, routes the request to a Python
    ``elicitation_handler``, and returns the response so the agent can
    continue."""
    from conduit_sdk import AgentOptions

    async def handler(req: ElicitationRequest) -> ElicitationResponse:
        assert req.mode == "form"
        assert "name" in (req.requested_schema or {}).get("properties", {})
        return ElicitationResponse(action="accept", content={"name": "grace"})

    options = AgentOptions(elicitation_handler=handler)
    client = Client([sys.executable, _APP], timeout=20, options=options)
    try:
        caps = await client.connect()
        assert caps is not None  # initialize handshake completed

        collected: list[str] = []
        async for message in client.prompt("anything"):
            collected.append(message.text())

        full = "".join(s for s in collected if s)
        assert "hello grace" in full, repr(collected)
    finally:
        await client.disconnect()
