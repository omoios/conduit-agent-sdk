"""Tests for tool schema generation and the SDK MCP server."""
from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from typing import Annotated, Optional

import pytest

from conduit_sdk.tools import (
    _infer_schema,
    create_sdk_mcp_server,
    tool,
)


# --- schema generation -----------------------------------------------------


async def _sample(
    a: int,
    b: str = "x",
    flag: Optional[bool] = None,
    items: list[int] = None,  # type: ignore[assignment]
    mapping: dict = None,  # type: ignore[assignment]
    named: Annotated[str, "the name"] = "n",
) -> None:
    return None


def test_infer_schema_types_and_required():
    schema = json.loads(_infer_schema(_sample))
    assert schema["type"] == "object"
    props = schema["properties"]
    assert props["a"] == {"type": "integer"}
    assert props["b"] == {"type": "string"}
    assert props["flag"] == {"type": "boolean"}
    assert props["items"] == {"type": "array", "items": {"type": "integer"}}
    assert props["mapping"] == {"type": "object"}
    assert props["named"] == {"type": "string", "description": "the name"}
    # Only `a` is required: everything else has a default or is Optional.
    assert schema["required"] == ["a"]


def test_infer_schema_unknown_type_falls_back_to_string():
    async def fn(x):  # type: ignore[no-untyped-def]
        return x

    schema = json.loads(_infer_schema(fn))
    assert schema["properties"]["x"] == {"type": "string"}
    assert schema["required"] == ["x"]


# --- in-process MCP dispatch ----------------------------------------------


@pytest.fixture()
def math_server():
    @tool(description="Add two integers")
    async def add(a: int, b: int) -> int:
        return a + b

    @tool(description="Echo text")
    async def echo(text: str) -> str:
        return text

    return create_sdk_mcp_server("math", tools=[add, echo])


@pytest.mark.asyncio
async def test_handle_initialize(math_server):
    resp = await math_server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert resp["result"]["serverInfo"]["name"] == "math"
    assert "tools" in resp["result"]["capabilities"]


@pytest.mark.asyncio
async def test_handle_ping(math_server):
    resp = await math_server.handle_request({"jsonrpc": "2.0", "id": 9, "method": "ping"})
    assert resp["result"] == {}


@pytest.mark.asyncio
async def test_handle_tools_list(math_server):
    resp = await math_server.handle_request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = {t["name"] for t in resp["result"]["tools"]}
    assert names == {"add", "echo"}
    add_def = next(t for t in resp["result"]["tools"] if t["name"] == "add")
    assert add_def["inputSchema"]["properties"]["a"]["type"] == "integer"
    assert add_def["inputSchema"]["required"] == ["a", "b"]


@pytest.mark.asyncio
async def test_handle_tools_call(math_server):
    resp = await math_server.handle_request(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "add", "arguments": {"a": 2, "b": 40}}}
    )
    assert resp["result"]["content"][0]["text"] == "42"
    assert resp["result"]["isError"] is False


@pytest.mark.asyncio
async def test_handle_unknown_method(math_server):
    resp = await math_server.handle_request({"jsonrpc": "2.0", "id": 4, "method": "foo/bar"})
    assert resp["error"]["code"] == -32601


@pytest.mark.asyncio
async def test_handle_unknown_tool(math_server):
    resp = await math_server.handle_request(
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
         "params": {"name": "nope", "arguments": {}}}
    )
    # Unknown tool is a protocol-level (invalid params) error.
    assert resp["error"]["code"] == -32602


@pytest.mark.asyncio
async def test_handle_tool_failure_is_error_result(math_server):
    # A tool that raises returns a result with isError=True (MCP convention),
    # NOT a JSON-RPC error.
    @tool(description="Always fails")
    async def fail_sdk() -> str:
        raise ValueError("intentional error")

    server = create_sdk_mcp_server("bad", tools=[fail_sdk])
    resp = await server.handle_request(
        {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
         "params": {"name": "fail_sdk", "arguments": {}}}
    )
    assert "error" not in resp
    assert resp["result"]["isError"] is True
    assert "intentional error" in resp["result"]["content"][0]["text"]


# --- HTTP transport --------------------------------------------------------


def _post(url: str, payload: dict) -> dict:
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310
        return json.loads(resp.read())


@pytest.mark.asyncio
async def test_http_serve_tools_list_and_call():
    @tool(description="Add two integers")
    async def add(a: int, b: int) -> int:
        return a + b

    server = create_sdk_mcp_server("math", tools=[add])
    url = await server.start()
    try:
        assert server.acp_config() == {"type": "http", "name": "math", "url": url, "headers": []}

        loop = asyncio.get_running_loop()
        listed = await loop.run_in_executor(
            None, _post, url, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        )
        assert listed["result"]["tools"][0]["name"] == "add"

        called = await loop.run_in_executor(
            None, _post, url,
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "add", "arguments": {"a": 7, "b": 8}}},
        )
        assert called["result"]["content"][0]["text"] == "15"
    finally:
        await server.stop()
    assert server.url is None


@pytest.mark.asyncio
async def test_http_404_for_non_mcp_path():
    server = create_sdk_mcp_server("m", tools=[])
    url = (await server.start()).replace("/mcp", "/other")

    def fetch() -> int | None:
        req = urllib.request.Request(url, data=b"{}", method="POST")
        try:
            urllib.request.urlopen(req, timeout=5)  # noqa: S310
            return None
        except urllib.error.HTTPError as exc:
            return exc.code

    try:
        loop = asyncio.get_running_loop()
        assert await loop.run_in_executor(None, fetch) == 404
    finally:
        await server.stop()


def test_create_sdk_mcp_server_rejects_non_tool():
    async def not_a_tool(x: int) -> int:
        return x

    with pytest.raises(Exception):
        create_sdk_mcp_server("bad", tools=[not_a_tool])
