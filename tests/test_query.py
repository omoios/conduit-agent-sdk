"""Tests for conduit_sdk.query (Query lifecycle management)."""

from __future__ import annotations

import json

import pytest

from conduit_sdk._conduit_sdk import RustControlProtocol
from conduit_sdk.permissions import (
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
)
from conduit_sdk.query import Query


class TestQueryInit:
    def test_default_state(self):
        protocol = RustControlProtocol()
        query = Query(protocol)
        assert query.protocol is protocol
        assert not query.initialized
        assert query._can_use_tool is None
        assert query._hook_callback is None
        assert query._mcp_callback is None

    def test_with_callbacks(self):
        protocol = RustControlProtocol()

        async def my_policy(name, input_, ctx):
            return PermissionResultAllow()

        async def my_hook(data):
            return {}

        query = Query(
            protocol,
            can_use_tool=my_policy,
            hook_callback=my_hook,
        )
        assert query._can_use_tool is my_policy
        assert query._hook_callback is my_hook


class TestQueryPermissionRouting:
    """Test that handle_control_request correctly routes permission messages."""

    @pytest.mark.asyncio
    async def test_non_control_message_ignored(self):
        protocol = RustControlProtocol()
        query = Query(protocol)
        # Non-JSON should not raise
        await query.handle_control_request("not json")

    @pytest.mark.asyncio
    async def test_non_control_type_ignored(self):
        protocol = RustControlProtocol()
        query = Query(protocol)
        msg = json.dumps({"type": "conversation", "data": {}})
        await query.handle_control_request(msg)

    @pytest.mark.asyncio
    async def test_permission_callback_invoked(self):
        """Verify the can_use_tool callback is called with correct args."""
        captured = {}

        async def capture_policy(name, input_, ctx):
            captured["tool_name"] = name
            captured["tool_input"] = input_
            captured["context"] = ctx
            return PermissionResultDeny("test denial")

        protocol = RustControlProtocol()
        query = Query(protocol, can_use_tool=capture_policy)

        msg = json.dumps({
            "type": "control",
            "request_id": "req_test",
            "subtype": "can_use_tool",
            "data": {
                "tool_name": "Bash",
                "tool_input": {"command": "ls"},
                "tool_use_id": "tu_1",
                "session_id": "sess_1",
            },
        })

        # This will fail on send_control_response since protocol isn't started,
        # but the callback itself should still be invoked.
        try:
            await query.handle_control_request(msg)
        except Exception:
            pass  # Expected: protocol not started

        assert captured.get("tool_name") == "Bash"
        assert isinstance(captured.get("context"), ToolPermissionContext)
        assert captured["context"].tool_use_id == "tu_1"


class TestQueryControlMethods:
    """Test Query's outbound control methods (without live protocol)."""

    def test_close_marks_closed(self):
        protocol = RustControlProtocol()
        query = Query(protocol)
        assert not query._closed
