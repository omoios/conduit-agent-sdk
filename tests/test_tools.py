"""Tests for conduit_sdk.tools (@tool decorator and MCP server)."""

from __future__ import annotations

import json

import pytest

from conduit_sdk import ToolSchema, tool
from conduit_sdk.exceptions import ToolError
from conduit_sdk.tools import (
    McpSdkServerConfig,
    _infer_schema,
    create_sdk_mcp_server,
    handle_mcp_request,
)


class TestToolDecorator:
    def test_basic_decoration(self):
        @tool(description="Say hello")
        async def greet(name: str) -> str:
            return f"Hello, {name}!"

        assert hasattr(greet, "_tool_definition")
        defn = greet._tool_definition
        assert defn.name == "greet"
        assert defn.description == "Say hello"

    def test_custom_name(self):
        @tool(name="my_tool", description="Custom tool")
        async def internal_fn() -> str:
            return "ok"

        assert internal_fn._tool_definition.name == "my_tool"

    def test_explicit_schema(self):
        schema = {"type": "object", "properties": {"x": {"type": "integer"}}}

        @tool(description="Add numbers", input_schema=schema)
        async def add(x: int) -> int:
            return x + 1

        parsed = json.loads(add._tool_definition.input_schema)
        assert parsed["properties"]["x"]["type"] == "integer"


class TestSchemaInference:
    def test_string_param(self):
        async def fn(name: str) -> str:
            return name

        schema = json.loads(_infer_schema(fn))
        assert schema["properties"]["name"]["type"] == "string"
        assert "name" in schema["required"]

    def test_int_param(self):
        async def fn(count: int) -> int:
            return count

        schema = json.loads(_infer_schema(fn))
        assert schema["properties"]["count"]["type"] == "integer"

    def test_optional_param(self):
        async def fn(name: str, title: str = "Mr") -> str:
            return f"{title} {name}"

        schema = json.loads(_infer_schema(fn))
        assert "name" in schema["required"]
        assert "title" not in schema["required"]

    def test_bool_param(self):
        async def fn(flag: bool) -> bool:
            return flag

        schema = json.loads(_infer_schema(fn))
        assert schema["properties"]["flag"]["type"] == "boolean"

    def test_float_param(self):
        async def fn(value: float) -> float:
            return value

        schema = json.loads(_infer_schema(fn))
        assert schema["properties"]["value"]["type"] == "number"


# ---------------------------------------------------------------------------
# SDK MCP Server tests
# ---------------------------------------------------------------------------


class TestMcpSdkServerConfig:
    def test_basic_creation(self):
        @tool(description="Echo a message")
        async def echo(msg: str) -> str:
            return msg

        config = McpSdkServerConfig(name="test-server", tools=[echo])
        assert config.name == "test-server"
        assert config.version == "1.0.0"
        assert len(config.tools) == 1

    def test_to_dict(self):
        @tool(description="Add two numbers")
        async def add(a: int, b: int) -> int:
            return a + b

        config = McpSdkServerConfig(name="math", version="2.0.0", tools=[add])
        d = config.to_dict()
        assert d["name"] == "math"
        assert d["version"] == "2.0.0"
        assert len(d["tools"]) == 1
        assert d["tools"][0]["name"] == "add"
        assert "inputSchema" in d["tools"][0]

    def test_get_tool_definitions(self):
        @tool(description="Greet someone")
        async def greet_sdk(name: str) -> str:
            return f"Hi {name}"

        config = McpSdkServerConfig(name="greet-server", tools=[greet_sdk])
        defs = config.get_tool_definitions()
        assert len(defs) == 1
        assert defs[0]["name"] == "greet_sdk"

    def test_get_tool_callback_found(self):
        @tool(description="Read a file")
        async def read_file_sdk(path: str) -> str:
            return "content"

        config = McpSdkServerConfig(name="fs", tools=[read_file_sdk])
        cb = config.get_tool_callback("read_file_sdk")
        assert cb is not None

    def test_get_tool_callback_not_found(self):
        config = McpSdkServerConfig(name="empty")
        cb = config.get_tool_callback("nonexistent")
        assert cb is None


class TestCreateSdkMcpServer:
    def test_from_decorated_tools(self):
        @tool(description="Ping")
        async def ping_sdk() -> str:
            return "pong"

        server = create_sdk_mcp_server("ping-server", tools=[ping_sdk])
        assert isinstance(server, McpSdkServerConfig)
        assert server.name == "ping-server"
        assert len(server.tools) == 1

    def test_undecorated_raises(self):
        async def bare_fn() -> str:
            return "hi"

        with pytest.raises(ToolError, match="not a registered @tool"):
            create_sdk_mcp_server("bad-server", tools=[bare_fn])

    def test_custom_version(self):
        @tool(description="Version test")
        async def ver_test_sdk() -> str:
            return "ok"

        server = create_sdk_mcp_server(
            "versioned", version="3.0.0", tools=[ver_test_sdk]
        )
        assert server.version == "3.0.0"


class TestHandleMcpRequest:
    @pytest.mark.asyncio
    async def test_tools_list(self):
        @tool(description="List files")
        async def ls_sdk(path: str) -> str:
            return "file1\nfile2"

        server = McpSdkServerConfig(name="fs", tools=[ls_sdk])
        servers = {"fs": server}

        result = await handle_mcp_request(
            servers,
            {"method": "tools/list", "server": "fs"},
        )
        assert "tools" in result
        assert len(result["tools"]) == 1
        assert result["tools"][0]["name"] == "ls_sdk"

    @pytest.mark.asyncio
    async def test_tools_list_all_servers(self):
        @tool(description="Tool A")
        async def tool_a_sdk() -> str:
            return "a"

        @tool(description="Tool B")
        async def tool_b_sdk() -> str:
            return "b"

        servers = {
            "s1": McpSdkServerConfig(name="s1", tools=[tool_a_sdk]),
            "s2": McpSdkServerConfig(name="s2", tools=[tool_b_sdk]),
        }

        result = await handle_mcp_request(
            servers,
            {"method": "tools/list"},
        )
        assert len(result["tools"]) == 2

    @pytest.mark.asyncio
    async def test_tools_call_success(self):
        @tool(description="Double a number")
        async def double_sdk(x: int) -> int:
            return x * 2

        server = McpSdkServerConfig(name="math", tools=[double_sdk])
        servers = {"math": server}

        result = await handle_mcp_request(
            servers,
            {
                "method": "tools/call",
                "server": "math",
                "params": {"name": "double_sdk", "arguments": {"x": 5}},
            },
        )
        assert "content" in result
        assert result["content"][0]["text"] == "10"

    @pytest.mark.asyncio
    async def test_tools_call_not_found(self):
        servers: dict[str, McpSdkServerConfig] = {}
        result = await handle_mcp_request(
            servers,
            {
                "method": "tools/call",
                "params": {"name": "missing", "arguments": {}},
            },
        )
        assert "error" in result
        assert "not found" in result["error"]

    @pytest.mark.asyncio
    async def test_tools_call_error(self):
        @tool(description="Always fails")
        async def fail_sdk() -> str:
            raise ValueError("intentional error")

        server = McpSdkServerConfig(name="bad", tools=[fail_sdk])
        servers = {"bad": server}

        result = await handle_mcp_request(
            servers,
            {
                "method": "tools/call",
                "server": "bad",
                "params": {"name": "fail_sdk", "arguments": {}},
            },
        )
        assert result.get("isError") is True
        assert "intentional error" in result["error"]

    @pytest.mark.asyncio
    async def test_unknown_method(self):
        result = await handle_mcp_request({}, {"method": "unknown/method"})
        assert "error" in result
