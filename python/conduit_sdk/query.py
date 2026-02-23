"""Query lifecycle management for conduit-agent-sdk.

The ``Query`` class wraps a single prompt-response cycle, handling
the control protocol exchange between SDK and agent. It routes
incoming control requests to the appropriate callbacks (permissions,
hooks, MCP) and manages initialization and shutdown.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from conduit_sdk._conduit_sdk import RustControlProtocol
from conduit_sdk.permissions import (
    PermissionResult,
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
)


class Query:
    """Manages the control request/response lifecycle for a single query.

    Parameters
    ----------
    protocol:
        The Rust control protocol instance.
    can_use_tool:
        Optional permission callback.
    hook_callback:
        Optional hook dispatch callback.
    mcp_callback:
        Optional MCP tool request callback.
    """

    def __init__(
        self,
        protocol: RustControlProtocol,
        *,
        can_use_tool: Callable | None = None,
        hook_callback: Callable | None = None,
        mcp_callback: Callable | None = None,
    ) -> None:
        self._protocol = protocol
        self._can_use_tool = can_use_tool
        self._hook_callback = hook_callback
        self._mcp_callback = mcp_callback
        self._initialized = False
        self._closed = False

    @property
    def protocol(self) -> RustControlProtocol:
        return self._protocol

    @property
    def initialized(self) -> bool:
        return self._initialized

    async def initialize(self, options_data: dict[str, Any] | None = None) -> dict:
        """Exchange capabilities with the agent via the control protocol.

        Sends an ``initialize`` control request with the agent options
        and receives the agent's capability advertisement.

        Returns
        -------
        dict:
            The agent's capabilities response.
        """
        payload = json.dumps(options_data or {})
        response_json = await self._protocol.send_control_request("initialize", payload)
        self._initialized = True

        try:
            return json.loads(response_json)
        except (json.JSONDecodeError, TypeError):
            return {}

    async def handle_control_request(self, raw_message: str) -> None:
        """Route an incoming control request by subtype.

        Parameters
        ----------
        raw_message:
            Raw JSON string of the control message from agent stdout.
        """
        try:
            msg = json.loads(raw_message)
        except json.JSONDecodeError:
            return

        if msg.get("type") != "control":
            return

        request_id = msg.get("request_id", "")
        subtype = msg.get("subtype", "")
        data = msg.get("data", {})

        if subtype == "can_use_tool":
            await self._handle_permission(request_id, data)
        elif subtype == "hook_callback":
            await self._handle_hook(request_id, data)
        elif subtype == "mcp_message":
            await self._handle_mcp(request_id, data)

    async def _handle_permission(self, request_id: str, data: Any) -> None:
        """Handle a permission check control request."""
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except json.JSONDecodeError:
                data = {}

        tool_name = data.get("tool_name", "")
        tool_input = json.dumps(data.get("tool_input", {}))
        tool_use_id = data.get("tool_use_id")
        session_id = data.get("session_id")

        context = ToolPermissionContext(
            tool_name=tool_name,
            tool_input=tool_input,
            tool_use_id=tool_use_id,
            session_id=session_id,
        )

        if self._can_use_tool is not None:
            result = await self._can_use_tool(tool_name, tool_input, context)
        else:
            result = PermissionResultAllow()

        if isinstance(result, PermissionResultDeny):
            response_data = json.dumps(
                {"decision": "deny", "reason": result.reason}
            )
        else:
            response_data = json.dumps({"decision": "allow"})

        await self._protocol.send_control_response(
            request_id, "can_use_tool", response_data
        )

    async def _handle_hook(self, request_id: str, data: Any) -> None:
        """Handle a hook callback control request."""
        if self._hook_callback is not None:
            result = await self._hook_callback(data)
            response_data = json.dumps(result if result is not None else {})
        else:
            response_data = json.dumps({})

        await self._protocol.send_control_response(
            request_id, "hook_callback", response_data
        )

    async def _handle_mcp(self, request_id: str, data: Any) -> None:
        """Handle an MCP tool request control message."""
        if self._mcp_callback is not None:
            result = await self._mcp_callback(data)
            response_data = json.dumps(result if result is not None else {})
        else:
            response_data = json.dumps({"error": "no MCP handler registered"})

        await self._protocol.send_control_response(
            request_id, "mcp_message", response_data
        )

    async def interrupt(self) -> None:
        """Send an interrupt control request to the agent."""
        await self._protocol.send_control_request(
            "interrupt", json.dumps({})
        )

    async def set_permission_mode(self, mode: str) -> None:
        """Change the permission mode mid-session."""
        await self._protocol.send_control_request(
            "set_permission_mode",
            json.dumps({"mode": mode}),
        )

    async def set_model(self, model: str) -> None:
        """Change the model mid-session."""
        await self._protocol.send_control_request(
            "set_model",
            json.dumps({"model": model}),
        )

    async def close(self) -> None:
        """Shut down the query and control protocol."""
        if not self._closed:
            await self._protocol.stop()
            self._closed = True
