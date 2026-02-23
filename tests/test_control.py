"""Tests for the control protocol wire types and message classification."""

from __future__ import annotations

import json

from conduit_sdk._conduit_sdk import ControlMessage, ControlResponse, RustControlProtocol


class TestControlMessage:
    def test_creation(self):
        msg = ControlMessage(
            request_id="req_1",
            subtype="can_use_tool",
            data='{"tool_name": "Bash"}',
        )
        assert msg.request_id == "req_1"
        assert msg.subtype == "can_use_tool"
        assert msg.data == '{"tool_name": "Bash"}'

    def test_repr(self):
        msg = ControlMessage("req_1", "can_use_tool", "{}")
        r = repr(msg)
        assert "req_1" in r
        assert "can_use_tool" in r


class TestControlResponse:
    def test_creation(self):
        resp = ControlResponse(
            request_id="req_1",
            subtype="can_use_tool",
            data='{"decision": "allow"}',
        )
        assert resp.request_id == "req_1"
        assert resp.subtype == "can_use_tool"
        parsed = json.loads(resp.data)
        assert parsed["decision"] == "allow"

    def test_repr(self):
        resp = ControlResponse("req_1", "can_use_tool", "{}")
        r = repr(resp)
        assert "req_1" in r


class TestRustControlProtocol:
    def test_instantiation(self):
        protocol = RustControlProtocol()
        assert protocol is not None
