"""Tests for conduit_sdk.options (AgentOptions)."""

from __future__ import annotations

from conduit_sdk.options import AgentOptions


class TestAgentOptionsDefaults:
    def test_all_defaults(self):
        opts = AgentOptions()
        assert opts.system_prompt is None
        assert opts.model is None
        assert opts.permission_mode is None
        assert opts.can_use_tool is None
        assert opts.tools is None
        assert opts.allowed_tools == []
        assert opts.disallowed_tools == []
        assert opts.mcp_servers is None
        assert opts.max_turns is None
        assert opts.cwd is None
        assert opts.env == {}
        assert opts.include_partial_messages is False
        assert opts.hooks is None


class TestAgentOptionsCustom:
    def test_custom_values(self):
        async def my_policy(name, input_, ctx):
            pass

        opts = AgentOptions(
            system_prompt="Be helpful",
            model="claude-sonnet-4-20250514",
            permission_mode="default",
            can_use_tool=my_policy,
            tools=["Bash", "Read"],
            allowed_tools=["Bash"],
            disallowed_tools=["Write"],
            max_turns=10,
            cwd="/tmp",
            env={"KEY": "value"},
            include_partial_messages=True,
        )
        assert opts.system_prompt == "Be helpful"
        assert opts.model == "claude-sonnet-4-20250514"
        assert opts.permission_mode == "default"
        assert opts.can_use_tool is my_policy
        assert opts.tools == ["Bash", "Read"]
        assert opts.allowed_tools == ["Bash"]
        assert opts.disallowed_tools == ["Write"]
        assert opts.max_turns == 10
        assert opts.cwd == "/tmp"
        assert opts.env == {"KEY": "value"}
        assert opts.include_partial_messages is True


class TestAgentOptionsToDict:
    def test_empty_options_to_dict(self):
        opts = AgentOptions()
        d = opts.to_dict()
        assert d == {}

    def test_populated_options_to_dict(self):
        opts = AgentOptions(
            system_prompt="Hello",
            model="claude-4",
            permission_mode="plan",
            tools=["Bash"],
            allowed_tools=["Bash"],
            max_turns=5,
            cwd="/home",
            env={"A": "B"},
            include_partial_messages=True,
        )
        d = opts.to_dict()
        assert d["systemPrompt"] == "Hello"
        assert d["model"] == "claude-4"
        assert d["permissionMode"] == "plan"
        assert d["tools"] == ["Bash"]
        assert d["allowedTools"] == ["Bash"]
        assert d["maxTurns"] == 5
        assert d["cwd"] == "/home"
        assert d["env"] == {"A": "B"}
        assert d["includePartialMessages"] is True

    def test_callback_not_serialized(self):
        async def cb(n, i, c):
            pass

        opts = AgentOptions(can_use_tool=cb)
        d = opts.to_dict()
        # can_use_tool is a callback, not serialized to dict
        assert "canUseTool" not in d

    def test_mcp_servers_dict_passthrough(self):
        opts = AgentOptions(
            mcp_servers={"my-server": {"command": ["node", "server.js"]}}
        )
        d = opts.to_dict()
        assert d["mcpServers"]["my-server"]["command"] == ["node", "server.js"]
