"""Tests for conduit_sdk.permissions (PermissionResult types and policies)."""

from __future__ import annotations

import pytest

from conduit_sdk.permissions import (
    PermissionResult,
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
    allow_all,
    deny_all,
)


class TestPermissionResultAllow:
    def test_is_permission_result(self):
        result = PermissionResultAllow()
        assert isinstance(result, PermissionResult)

    def test_repr(self):
        result = PermissionResultAllow()
        assert "Allow" in repr(result)


class TestPermissionResultDeny:
    def test_is_permission_result(self):
        result = PermissionResultDeny("not allowed")
        assert isinstance(result, PermissionResult)

    def test_reason(self):
        result = PermissionResultDeny("Bash not allowed")
        assert result.reason == "Bash not allowed"

    def test_default_reason(self):
        result = PermissionResultDeny()
        assert result.reason == ""

    def test_repr(self):
        result = PermissionResultDeny("nope")
        assert "nope" in repr(result)


class TestToolPermissionContext:
    def test_basic(self):
        ctx = ToolPermissionContext(
            tool_name="Bash",
            tool_input='{"command": "ls"}',
        )
        assert ctx.tool_name == "Bash"
        assert ctx.tool_input == '{"command": "ls"}'
        assert ctx.tool_use_id is None
        assert ctx.session_id is None

    def test_full(self):
        ctx = ToolPermissionContext(
            tool_name="Read",
            tool_input='{"path": "/tmp/file"}',
            tool_use_id="tu_abc",
            session_id="sess_123",
        )
        assert ctx.tool_use_id == "tu_abc"
        assert ctx.session_id == "sess_123"


class TestBuiltInPolicies:
    @pytest.mark.asyncio
    async def test_allow_all(self):
        ctx = ToolPermissionContext(tool_name="Bash", tool_input="{}")
        result = await allow_all("Bash", "{}", ctx)
        assert isinstance(result, PermissionResultAllow)

    @pytest.mark.asyncio
    async def test_deny_all(self):
        ctx = ToolPermissionContext(tool_name="Bash", tool_input="{}")
        result = await deny_all("Bash", "{}", ctx)
        assert isinstance(result, PermissionResultDeny)
        assert "denied by policy" in result.reason


class TestPermissionError:
    def test_importable(self):
        from conduit_sdk.exceptions import PermissionError

        err = PermissionError("denied")
        assert str(err) == "denied"

    def test_inherits_conduit_error(self):
        from conduit_sdk.exceptions import ConduitError, PermissionError

        err = PermissionError("test")
        assert isinstance(err, ConduitError)
