"""Tool registration and MCP server creation.

Provides the ``@tool`` decorator for defining tools that agents can invoke,
with JSON-Schema generation from type hints. Tools are served to agents via
an in-process MCP server (:class:`McpSdkServerConfig`) exposed over a local
HTTP transport, so any ACP agent that supports ``http`` MCP servers (Claude
Code, Codex, ...) can discover and call them.

Example::

    from conduit_sdk import AgentOptions, Client, create_sdk_mcp_server, tool

    @tool(description="Add two numbers")
    async def add(a: int, b: int) -> int:
        return a + b

    server = create_sdk_mcp_server("math", tools=[add])
    options = AgentOptions(mcp_servers={"math": server})
    async with Client(["claude", "--agent"], options=options) as client:
        async for message in client.prompt("What is 2+2?"):
            print(message.text())
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, get_args, get_origin, get_type_hints

from conduit_sdk._conduit_sdk import RustToolRegistry, ToolDefinition
from conduit_sdk.exceptions import ToolError

__all__ = [
    "tool",
    "create_mcp_server",
    "create_sdk_mcp_server",
    "McpSdkServerConfig",
]

# Global tool registry used by the @tool decorator.
_registry = RustToolRegistry()

# Tools registered via the decorator are collected here and bulk-registered
# when a client connects / a server is created.
_pending_registrations: list[tuple[ToolDefinition, Callable]] = []


# ---------------------------------------------------------------------------
# @tool decorator + schema inference
# ---------------------------------------------------------------------------


def tool(
    name: str | None = None,
    *,
    description: str = "",
    input_schema: dict[str, Any] | None = None,
) -> Callable:
    """Register an async function as an ACP/MCP tool.

    Parameters
    ----------
    name:
        Tool name exposed to the agent. Defaults to the function name.
    description:
        Human-readable description of what the tool does.
    input_schema:
        JSON Schema dict describing the tool's input parameters.
        If omitted, one is generated from the function's type hints.
    """

    def decorator(fn: Callable) -> Callable:
        tool_name = name or fn.__name__
        schema_json = (
            json.dumps(input_schema)
            if input_schema is not None
            else _infer_schema(fn)
        )
        definition = ToolDefinition(
            name=tool_name,
            description=description or inspect.getdoc(fn) or "",
            input_schema=schema_json,
        )
        _pending_registrations.append((definition, fn))

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            return await fn(*args, **kwargs)

        wrapper._tool_definition = definition  # type: ignore[attr-defined]
        return wrapper

    return decorator


async def register_pending_tools() -> None:
    """Register all ``@tool``-decorated functions with the Rust registry."""
    for definition, callback in _pending_registrations:
        await _registry.register(definition, callback)
    _pending_registrations.clear()


def get_registry() -> RustToolRegistry:
    """Return the global Rust tool registry."""
    return _registry


_SCALAR_TYPES: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}


def _json_type(hint: Any) -> tuple[str, dict[str, Any]]:
    """Map a Python type hint to a (json type, schema fragment) pair.

    Returns ("", {}) for unknown/Any types (omitted from the schema).
    """
    # Bare container types (unsubscripted).
    if hint is dict:
        return "object", {"type": "object"}
    if hint in (list, tuple, set, frozenset):
        return "array", {"type": "array"}
    if hint is type(None):
        return "", {}

    origin = get_origin(hint)
    if origin in (list, tuple, set, frozenset):
        args = [a for a in get_args(hint) if a is not type(None)]
        _, item_schema = (_json_type(args[0]) if args else ("string", {}))
        item = item_schema or {"type": "string"}
        return "array", {"type": "array", "items": item}
    if origin is dict:
        return "object", {"type": "object"}

    # Optional[X] / X | None -> unwrap the non-None member.
    try:
        from typing import Union  # noqa: PLC0415

        if origin is Union:
            members = [a for a in get_args(hint) if a is not type(None)]
            return _json_type(members[0]) if members else ("", {})
    except ImportError:  # pragma: no cover
        pass

    scalar = _SCALAR_TYPES.get(hint)
    if scalar:
        return scalar, {"type": scalar}
    return "", {}


def _infer_schema(fn: Callable) -> str:
    """Generate a JSON Schema (object) from a function's type hints.

    Handles scalars, ``Optional[X]`` / ``X | None``, ``list[X]``, ``dict``,
    default values (made non-required), and ``Annotated[T, "description"]``
    parameter descriptions.
    """
    sig = inspect.signature(fn)
    try:
        hints = get_type_hints(fn, include_extras=True)
    except Exception:  # noqa: BLE001 - hints may be unresolvable
        hints = {}

    properties: dict[str, Any] = {}
    required: list[str] = []

    for pname, param in sig.parameters.items():
        if pname in ("self", "cls"):
            continue
        hint = hints.get(pname, param.annotation)
        if hint is inspect.Parameter.empty:
            hint = str

        description: str | None = None
        unwrapped = hint
        origin = get_origin(hint)
        if origin is not None:
            try:
                from typing import Annotated  # noqa: PLC0415

                if origin is Annotated:
                    args = get_args(hint)
                    unwrapped = args[0]
                    for meta in args[1:]:
                        if isinstance(meta, str):
                            description = meta
                            break
            except ImportError:  # pragma: no cover
                pass

        # Optional -> not required.
        is_optional = _is_optional(hint)
        jt, frag = _json_type(unwrapped if unwrapped is not None else hint)
        prop: dict[str, Any] = frag if frag else ({"type": jt} if jt else {})
        if description:
            prop["description"] = description
        properties[pname] = prop or {"type": "string"}

        has_default = param.default is not inspect.Parameter.empty
        if not has_default and not is_optional:
            required.append(pname)

    return json.dumps(
        {"type": "object", "properties": properties, "required": required}
    )


def _is_optional(hint: Any) -> bool:
    origin = get_origin(hint)
    if origin is None:
        return False
    try:
        from typing import Union  # noqa: PLC0415

        if origin is Union:
            return type(None) in get_args(hint)
    except ImportError:  # pragma: no cover
        pass
    return False


# ---------------------------------------------------------------------------
# SDK MCP server -- serves @tool functions to agents over HTTP
# ---------------------------------------------------------------------------


_MCP_PROTOCOL_VERSION = "2024-11-05"


@dataclass
class McpSdkServerConfig:
    """An in-process MCP server hosting ``@tool`` functions.

    Pass instances to :class:`~conduit_sdk.AgentOptions` under
    ``mcp_servers``. The :class:`~conduit_sdk.Client` starts a local HTTP
    MCP server for each one on connect and stops them on disconnect, so the
    agent discovers and calls the tools over the standard MCP protocol.

    Parameters
    ----------
    name:
        Display name for the MCP server.
    version:
        Server version string.
    tools:
        List of ``@tool``-decorated async functions.
    """

    name: str
    version: str = "1.0.0"
    tools: list[Callable] = field(default_factory=list)

    # Runtime state for the HTTP server (set by start()).
    _server: Any = field(default=None, repr=False, compare=False)
    _url: str | None = field(default=None, repr=False, compare=False)

    # -- MCP tool manifest ------------------------------------------------

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        """Return MCP-formatted tool definitions for ``tools/list``."""
        definitions: list[dict[str, Any]] = []
        for fn in self.tools:
            defn = getattr(fn, "_tool_definition", None)
            if defn is not None:
                definitions.append(
                    {
                        "name": defn.name,
                        "description": defn.description,
                        "inputSchema": json.loads(defn.input_schema),
                    }
                )
        return definitions

    def get_tool_callback(self, tool_name: str) -> Callable | None:
        """Find the callback for a registered tool by name."""
        for fn in self.tools:
            defn = getattr(fn, "_tool_definition", None)
            if defn is not None and defn.name == tool_name:
                return fn
        return None

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dict (manifest form; see :meth:`acp_config`)."""
        return {
            "name": self.name,
            "version": self.version,
            "tools": self.get_tool_definitions(),
        }

    # -- MCP JSON-RPC dispatch -------------------------------------------

    async def handle_request(self, rpc: dict[str, Any]) -> dict[str, Any]:
        """Dispatch a single MCP JSON-RPC request and return the response.

        Handles ``initialize``, ``ping``, ``tools/list`` and ``tools/call``.
        Pure (no transport) \u2014 used directly by tests and by the HTTP layer.
        """
        method = rpc.get("method")
        req_id = rpc.get("id")
        result: Any = None
        error: dict[str, Any] | None = None
        try:
            if method == "initialize":
                result = {
                    "protocolVersion": _MCP_PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": self.name, "version": self.version},
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": self.get_tool_definitions()}
            elif method == "tools/call":
                params = rpc.get("params") or {}
                name = params.get("name")
                callback = self.get_tool_callback(name or "")
                if callback is None:
                    # Unknown tool is a protocol-level (invalid params) error.
                    error = {"code": -32602, "message": f"unknown tool: {name!r}"}
                else:
                    try:
                        output = await callback(**(params.get("arguments") or {}))
                        result = {"content": [_to_content_block(output)], "isError": False}
                    except Exception as exc:  # noqa: BLE001 - tool ran but failed
                        # MCP convention: a tool that raises returns a result
                        # with isError=True, NOT a JSON-RPC error.
                        result = {
                            "content": [
                                {"type": "text", "text": f"{type(exc).__name__}: {exc}"}
                            ],
                            "isError": True,
                        }
            else:
                error = {"code": -32601, "message": f"method not found: {method!r}"}
        except Exception as exc:  # noqa: BLE001
            error = {"code": -32603, "message": f"{type(exc).__name__}: {exc}"}
        resp: dict[str, Any] = {"jsonrpc": "2.0", "id": req_id}
        if error is not None:
            resp["error"] = error
        else:
            resp["result"] = result
        return resp

    # ``_call_tool`` is intentionally inlined in handle_request so that tool
    # execution failures map to isError results (not JSON-RPC errors).

    # -- HTTP transport ---------------------------------------------------

    async def start(self, host: str = "127.0.0.1", port: int = 0) -> str:
        """Start a local HTTP MCP server; return its URL.

        Idempotent: returns the existing URL if already running.
        """
        if self._server is not None:
            assert self._url is not None
            return self._url
        self._server = await asyncio.start_server(
            self._handle_connection, host, port
        )
        sockname = self._server.sockets[0].getsockname()
        self._url = f"http://{sockname[0]}:{sockname[1]}/mcp"
        return self._url

    async def stop(self) -> None:
        """Stop the HTTP server if running."""
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None
        self._url = None

    @property
    def url(self) -> str | None:
        return self._url

    def acp_config(self) -> dict[str, Any]:
        """Return the ACP ``McpServer`` HTTP config (after :meth:`start`).

        ``{"type": "http", "name": ..., "url": ...}`` \u2014 reachable by any
        agent that supports ``http`` MCP servers.
        """
        if self._url is None:
            raise ToolError("SDK MCP server not started; call start() or pass it to AgentOptions.mcp_servers")
        return {"type": "http", "name": self.name, "url": self._url, "headers": []}

    async def _handle_connection(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            await self._serve_one(reader, writer)
        except Exception:  # noqa: BLE001 - never let one bad conn kill the server
            pass  # noqa: BLE001 - never let one bad conn kill the server
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass

    async def _serve_one(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        request_line = await reader.readline()
        if not request_line:
            return
        parts = request_line.decode("latin-1").split()
        if len(parts) < 2:
            self._write_http(writer, 400, b"bad request")
            return
        method, path = parts[0], parts[1]
        headers: dict[str, str] = {}
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"\n", b""):
                break
            key, _, value = line.decode("latin-1").partition(":")
            headers[key.strip().lower()] = value.strip()

        length = int(headers.get("content-length") or "0")
        body = await reader.readexactly(length) if length > 0 else b""

        if method != "POST" or path.rstrip("/") != "/mcp":
            self._write_http(writer, 404, b'{"error":"not found"}')
            return

        try:
            payload = json.loads(body.decode("utf-8")) if body else {}
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._write_http(writer, 400, b'{"error":"invalid json"}')
            return

        if isinstance(payload, list):
            response = [await self.handle_request(r) for r in payload]
        else:
            response = await self.handle_request(payload)
        self._write_http(writer, 200, json.dumps(response).encode("utf-8"))

    @staticmethod
    def _write_http(writer: asyncio.StreamWriter, status: int, body: bytes) -> None:
        reason = {200: "OK", 400: "Bad Request", 404: "Not Found"}.get(status, "OK")
        head = (
            f"HTTP/1.1 {status} {reason}\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode("latin-1")
        writer.write(head)
        writer.write(body)


def _to_content_block(output: Any) -> dict[str, Any]:
    """Coerce a tool return value into an MCP content block."""
    if isinstance(output, dict) and {"type"}.issubset(output.keys()):
        return output
    if isinstance(output, dict):
        return {"type": "text", "text": json.dumps(output)}
    if isinstance(output, (list, tuple)) and output and isinstance(output[0], dict):
        # Caller returned a list of content blocks.
        return output[0]
    return {"type": "text", "text": "" if output is None else str(output)}


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def create_sdk_mcp_server(
    name: str,
    *,
    version: str = "1.0.0",
    tools: list[Callable] | None = None,
) -> McpSdkServerConfig:
    """Create an SDK MCP server from ``@tool``-decorated functions."""
    if tools is None:
        tools = [fn for _, fn in _pending_registrations]
    for fn in tools:
        if not hasattr(fn, "_tool_definition"):
            raise ToolError(f"{fn.__name__} is not a registered @tool")
    return McpSdkServerConfig(name=name, version=version, tools=list(tools))


async def create_mcp_server(
    name: str,
    tools: list[Callable] | None = None,
) -> dict[str, Any]:
    """Return a plain manifest dict of the named tools (no server).

    For a *serving* MCP server, use :func:`create_sdk_mcp_server` instead.
    """
    server = create_sdk_mcp_server(name, tools=tools)
    return server.to_dict()
