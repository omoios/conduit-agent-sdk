"""Tool registration and MCP server creation.

Provides the ``@tool`` decorator for defining tools that agents can
invoke, and a factory for creating in-process MCP servers from
registered tools.

Also provides ``McpSdkServerConfig`` for serving SDK-registered tools
to agents via the control protocol, and ``create_sdk_mcp_server()``
for building the config from ``@tool``-decorated functions.
"""

from __future__ import annotations

import functools
import inspect
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from conduit_sdk._conduit_sdk import RustToolRegistry, ToolDefinition
from conduit_sdk.exceptions import ToolError


# Global tool registry used by the @tool decorator.
_registry = RustToolRegistry()


def tool(
    name: str | None = None,
    *,
    description: str = "",
    input_schema: dict[str, Any] | None = None,
) -> Callable:
    """Decorator to register an async function as an ACP tool.

    Parameters
    ----------
    name:
        Tool name exposed to the agent. Defaults to the function name.
    description:
        Human-readable description of what the tool does.
    input_schema:
        JSON Schema dict describing the tool's input parameters.
        If omitted, a minimal schema is generated from the function
        signature.

    Example::

        @tool(description="Read a file from disk")
        async def read_file(path: str) -> str:
            return open(path).read()
    """

    def decorator(fn: Callable) -> Callable:
        tool_name = name or fn.__name__

        if input_schema is not None:
            schema_json = json.dumps(input_schema)
        else:
            schema_json = _infer_schema(fn)

        definition = ToolDefinition(
            name=tool_name,
            description=description or fn.__doc__ or "",
            input_schema=schema_json,
        )

        # Register synchronously at decoration time. The Rust side
        # stores the callback for later async invocation.
        # NOTE: In production this needs to be awaited. For now the
        # decorator is sync and registration is deferred to connect().
        _pending_registrations.append((definition, fn))

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            return await fn(*args, **kwargs)

        wrapper._tool_definition = definition  # type: ignore[attr-defined]
        return wrapper

    return decorator


# Tools registered via the decorator are collected here and bulk-registered
# when the client connects.
_pending_registrations: list[tuple[ToolDefinition, Callable]] = []


async def register_pending_tools() -> None:
    """Register all ``@tool``-decorated functions with the Rust registry."""
    for definition, callback in _pending_registrations:
        await _registry.register(definition, callback)
    _pending_registrations.clear()


def get_registry() -> RustToolRegistry:
    """Return the global tool registry."""
    return _registry


def _infer_schema(fn: Callable) -> str:
    """Generate a minimal JSON Schema from a function's type hints."""
    sig = inspect.signature(fn)
    hints = inspect.get_annotations(fn, eval_str=True)
    properties: dict[str, Any] = {}
    required: list[str] = []

    _TYPE_MAP: dict[type, str] = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
    }

    for param_name, param in sig.parameters.items():
        if param_name == "self":
            continue
        hint = hints.get(param_name)
        prop: dict[str, str] = {"type": _TYPE_MAP.get(hint, "string")}
        properties[param_name] = prop
        if param.default is inspect.Parameter.empty:
            required.append(param_name)

    schema = {"type": "object", "properties": properties, "required": required}
    return json.dumps(schema)


async def create_mcp_server(
    name: str,
    tools: list[Callable] | None = None,
) -> dict[str, Any]:
    """Create an in-process MCP server configuration from registered tools.

    Parameters
    ----------
    name:
        Display name for the MCP server.
    tools:
        Specific tool functions to include. If ``None``, all registered
        tools are included.

    Returns
    -------
    dict:
        MCP server configuration suitable for passing to the client.
    """
    tool_list = tools or [fn for _, fn in _pending_registrations]
    definitions = []
    for fn in tool_list:
        defn = getattr(fn, "_tool_definition", None)
        if defn is None:
            raise ToolError(f"{fn.__name__} is not a registered @tool")
        definitions.append(
            {
                "name": defn.name,
                "description": defn.description,
                "input_schema": json.loads(defn.input_schema),
            }
        )

    return {
        "name": name,
        "tools": definitions,
    }


# ---------------------------------------------------------------------------
# SDK MCP Server — serves @tool functions to agents via control protocol
# ---------------------------------------------------------------------------


@dataclass
class McpSdkServerConfig:
    """Configuration for an SDK-hosted MCP server.

    Holds the server name, version, and registered tool functions.
    When the agent sends a ``tools/list`` or ``tools/call`` MCP request
    via the control protocol, the SDK uses this config to respond.

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

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dict for the control protocol options payload."""
        definitions = []
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
        return {
            "name": self.name,
            "version": self.version,
            "tools": definitions,
        }

    def get_tool_definitions(self) -> list[dict[str, Any]]:
        """Return MCP-formatted tool definitions for ``tools/list``."""
        definitions = []
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


def create_sdk_mcp_server(
    name: str,
    *,
    version: str = "1.0.0",
    tools: list[Callable] | None = None,
) -> McpSdkServerConfig:
    """Create an SDK MCP server config from ``@tool``-decorated functions.

    Parameters
    ----------
    name:
        Display name for the MCP server.
    version:
        Server version string.
    tools:
        Specific tool functions to include. If ``None``, all pending
        ``@tool``-decorated functions are included.

    Returns
    -------
    McpSdkServerConfig:
        Config that can be passed to ``AgentOptions.mcp_servers``.

    Example::

        @tool(description="Query the database")
        async def query_db(sql: str) -> str:
            ...

        server = create_sdk_mcp_server("my-tools", tools=[query_db])
    """
    if tools is None:
        tools = [fn for _, fn in _pending_registrations]

    # Validate all functions are @tool-decorated.
    for fn in tools:
        if not hasattr(fn, "_tool_definition"):
            raise ToolError(f"{fn.__name__} is not a registered @tool")

    return McpSdkServerConfig(name=name, version=version, tools=list(tools))


async def handle_mcp_request(
    servers: dict[str, McpSdkServerConfig],
    data: Any,
) -> dict[str, Any]:
    """Route an MCP request from the agent to the appropriate SDK server.

    Handles ``tools/list`` and ``tools/call`` methods.

    Parameters
    ----------
    servers:
        Map of server name to config.
    data:
        The MCP request payload (parsed JSON).

    Returns
    -------
    dict:
        The MCP response to send back to the agent.
    """
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError:
            return {"error": "invalid MCP request"}

    method = data.get("method", "")
    server_name = data.get("server", "")
    params = data.get("params", {})

    server = servers.get(server_name)

    if method == "tools/list":
        # Aggregate tools from all servers if no specific server requested.
        if server is not None:
            tools = server.get_tool_definitions()
        else:
            tools = []
            for srv in servers.values():
                tools.extend(srv.get_tool_definitions())
        return {"tools": tools}

    elif method == "tools/call":
        tool_name = params.get("name", "")
        tool_input = params.get("arguments", {})

        # Find the callback across all servers.
        callback = None
        if server is not None:
            callback = server.get_tool_callback(tool_name)
        else:
            for srv in servers.values():
                callback = srv.get_tool_callback(tool_name)
                if callback is not None:
                    break

        if callback is None:
            return {"error": f"tool {tool_name!r} not found"}

        try:
            if isinstance(tool_input, str):
                tool_input = json.loads(tool_input)
            result = await callback(**tool_input)
            return {"content": [{"type": "text", "text": str(result)}]}
        except Exception as e:
            return {"error": str(e), "isError": True}

    return {"error": f"unknown MCP method: {method!r}"}
