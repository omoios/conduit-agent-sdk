"""Tests for conduit_sdk.Client."""

from __future__ import annotations

import pytest

from conduit_sdk import Client
from conduit_sdk.exceptions import ConnectionError
from conduit_sdk.options import AgentOptions
from conduit_sdk.permissions import PermissionResultAllow, PermissionResultDeny


class TestClientInit:
    def test_default_config(self):
        client = Client(["claude", "--agent"])
        assert not client.connected
        assert client.capabilities is None
        assert repr(client).startswith("Client(")

    def test_custom_config(self):
        client = Client(
            ["goose"],
            cwd="/tmp",
            env={"KEY": "value"},
            timeout=60,
        )
        assert not client.connected

    def test_hooks_accessible(self):
        client = Client(["claude"])
        assert client.hooks is not None


class TestClientPromptGuard:
    @pytest.mark.asyncio
    async def test_prompt_without_connect_raises(self):
        client = Client(["echo", "hi"])
        with pytest.raises(ConnectionError, match="not connected"):
            async for _ in client.prompt("hello"):
                pass


class TestClientWithOptions:
    def test_options_stored(self):
        opts = AgentOptions(model="claude-4", permission_mode="default")
        client = Client(["claude", "--agent"], options=opts)
        assert client.options is opts
        assert client.options.model == "claude-4"

    def test_options_override_cwd(self):
        opts = AgentOptions(cwd="/opt/work")
        client = Client(["claude"], cwd="/tmp", options=opts)
        assert client._config.cwd == "/opt/work"

    def test_options_merge_env(self):
        opts = AgentOptions(env={"B": "2"})
        client = Client(["claude"], env={"A": "1"}, options=opts)
        assert client._config.env["A"] == "1"
        assert client._config.env["B"] == "2"

    def test_no_options_no_query(self):
        client = Client(["claude"])
        assert client.query is None
        assert client.options is None

    def test_with_permission_callback(self):
        async def policy(name, input_, ctx):
            return PermissionResultAllow()

        opts = AgentOptions(can_use_tool=policy)
        client = Client(["claude"], options=opts)
        assert client.options.can_use_tool is policy

    def test_repr_includes_options(self):
        opts = AgentOptions(model="claude-4")
        client = Client(["claude"], options=opts)
        r = repr(client)
        assert "options=" in r


class TestClientRepr:
    def test_disconnected(self):
        client = Client(["agent"])
        assert "disconnected" in repr(client)
