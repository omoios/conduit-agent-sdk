"""Agent/server side \u2014 turn Python into an ACP agent.

This module lets you author an ACP-compatible agent that a client (this SDK's
:class:`~conduit_sdk.Client`, or any ACP client) can spawn and talk to over
stdio. It mirrors the authoring model of the official
``agentclientprotocol/python-sdk``: register async handlers, then
``server.run()`` speaks the protocol.

Minimal agent (echo)::

    from conduit_sdk import AgentServer

    server = AgentServer(name="echo")

    @server.on_prompt
    async def echo(ctx, session_id, content):
        text = "".join(b.get("text", "") for b in content if isinstance(b, dict))
        await ctx.send_text("echo: " + text)
        return "end_turn"   # stop reason

    if __name__ == "__main__":
        server.run()

The agent is driven over stdio (newline-delimited JSON-RPC 2.0), so it runs as
a subprocess spawned by a client. Streaming is *push-based*: inside the prompt
handler you call ``await ctx.send_text(...)`` (one ``session/update``
notification per chunk) and the return value is the turn's ``stop_reason``.
"""

from __future__ import annotations

import asyncio
import json
import sys
import urllib.error
import urllib.request
import uuid
from typing import Any, Awaitable, Callable

__all__ = ["AgentServer", "AgentContext"]

# JSON-RPC method names (ACP v1).
_INITIALIZE = "initialize"
_SESSION_NEW = "session/new"
_SESSION_LOAD = "session/load"
_SESSION_PROMPT = "session/prompt"
_SESSION_CANCEL = "session/cancel"
_SESSION_UPDATE = "session/update"
_ELICITATION_CREATE = "elicitation/create"
_SESSION_DELETE = "session/delete"

_STOP_REASONS = {
    "end_turn",
    "max_tokens",
    "max_turn_requests",
    "refusal",
    "cancelled",
}


class AgentContext:
    """Streaming handle passed to a prompt handler.

    Each ``send_*`` call emits a ``session/update`` notification to the client.
    For update kinds beyond text/thought (tool calls, plan, usage, ...), build
    the raw update dict and pass it to :meth:`send_update`.
    """

    def __init__(
        self,
        session_id: str,
        write: Callable[[dict[str, Any]], Awaitable[None]],
        mcp_servers: list[dict[str, Any]] | None = None,
        request_sender: Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]] | None = None,
    ) -> None:
        self.session_id = session_id
        self._write = write
        self.mcp_servers = mcp_servers or []
        self._request_sender = request_sender

    async def send_text(self, text: str) -> None:
        """Stream an ``agent_message_chunk`` with a text block."""
        await self.send_update(
            {
                "sessionUpdate": "agent_message_chunk",
                "content": {"type": "text", "text": text},
            }
        )

    async def send_thought(self, text: str) -> None:
        """Stream an ``agent_thought_chunk`` with a text block."""
        await self.send_update(
            {
                "sessionUpdate": "agent_thought_chunk",
                "content": {"type": "text", "text": text},
            }
        )

    async def tool_call(
        self,
        tool_use_id: str,
        title: str,
        kind: str = "other",
        raw_input: Any = None,
        status: str = "pending",
    ) -> None:
        """Emit a ``tool_call`` session update (tool-start ACP event).

        Args:
            tool_use_id: Unique identifier for this tool invocation.
            title: Human-readable tool name/title.
            kind: ACP ``ToolKind`` value (e.g. ``"read"``, ``"search"``, ``"other"``).
            raw_input: Arbitrary JSON-serialisable input the tool received.
            status: Tool lifecycle status (``"pending"``, ``"in_progress"``, etc.).
        """
        await self.send_update(
            {
                "sessionUpdate": "tool_call",
                "toolCallId": tool_use_id,
                "title": title,
                "kind": kind,
                "status": status,
                "rawInput": raw_input,
            }
        )

    async def tool_result(
        self,
        tool_use_id: str,
        status: str,
        output: str | None = None,
        locations: list | None = None,
    ) -> None:
        """Emit a ``tool_call_update`` session update (tool-result ACP event).

        Args:
            tool_use_id: Must match the id passed to the corresponding
                :meth:`tool_call`.
            status: Result status (``"completed"``, ``"failed"``, etc.).
            output: Text output for the tool result content block.
            locations: Optional list of file-system location dicts.
        """
        d: dict[str, Any] = {
            "sessionUpdate": "tool_call_update",
            "toolCallId": tool_use_id,
            "status": status,
        }
        if output is not None:
            d["content"] = [
                {"type": "content", "content": {"type": "text", "text": output}}
            ]
        if locations is not None:
            d["locations"] = locations
        await self.send_update(d)

    async def plan(self, entries: list) -> None:
        """Emit a ``plan`` session update.

        Args:
            entries: A list of plan-entry dicts (each typically has
                ``"goal"``, ``"status"``, ``"subtext"``, etc.).
        """
        await self.send_update(
            {"sessionUpdate": "plan", "entries": entries}
        )

    async def usage(self, used: int | None = None, size: int | None = None) -> None:
        """Emit a ``usage`` session update.

        Args:
            used: Number of tokens / units used (default 0).
            size: Total size / limit (default 0).
        """
        await self.send_update(
            {
                "sessionUpdate": "usage_update",
                "used": used if used is not None else 0,
                "size": size if size is not None else 0,
            }
        )

    async def mode_change(self, mode_id: str) -> None:
        """Emit a ``current_mode_update`` session update to switch modes.

        Args:
            mode_id: The new mode identifier (e.g. ``"build"``, ``"chat"``).
        """
        await self.send_update(
            {
                "sessionUpdate": "current_mode_update",
                "currentModeId": mode_id,
            }
        )

    async def send_update(self, update: dict[str, Any]) -> None:
        """Send an arbitrary session update dict.

        ``update`` must carry a ``sessionUpdate`` discriminator matching an ACP
        ``SessionUpdate`` variant, e.g. ``{"sessionUpdate": "tool_call", ...}``.
        """
        await self._write(
            {
                "jsonrpc": "2.0",
                "method": _SESSION_UPDATE,
                "params": {"sessionId": self.session_id, "update": update},
            }
        )

    async def call_tool(
        self,
        server_name: str,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Call a tool on an ``http`` MCP server the client provided at
        ``session/new``. Returns the MCP ``tools/call`` result dict
        (``{"content": [...], "isError": bool}``).
        """
        server = next(
            (s for s in self.mcp_servers if s.get("name") == server_name), None
        )
        if server is None:
            raise KeyError(
                f"MCP server {server_name!r} was not provided to this session"
            )
        if server.get("type") != "http" or not server.get("url"):
            raise ValueError(
                f"MCP server {server_name!r} is not an http server"
            )
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, _mcp_call, server["url"], tool_name, arguments or {}
        )

    async def request_elicitation(
        self,
        message: str,
        *,
        requested_schema: dict[str, Any] | None = None,
        mode: str = "form",
        url: str | None = None,
        elicitation_id: str | None = None,
        tool_call_id: str | None = None,
    ) -> dict[str, Any]:
        """Request structured user input from the client (**UNSTABLE**).

        Sends an ``elicitation/create`` request and awaits the client's
        response. For ``form`` mode, ``requested_schema`` is a JSON Schema
        object describing the form fields. For ``url`` mode, both ``url`` and
        ``elicitation_id`` are required.

        Returns the raw response dict: ``{"action": "accept", "content": ...}``,
        ``{"action": "decline"}``, or ``{"action": "cancel"}``.
        """
        if self._request_sender is None:
            raise RuntimeError(
                "this agent transport cannot send requests to the client"
            )
        params: dict[str, Any] = {
            "message": message,
            "mode": mode,
            "sessionId": self.session_id,
        }
        if tool_call_id:
            params["toolCallId"] = tool_call_id
        if mode == "form":
            if requested_schema is None:
                raise ValueError("form elicitation requires requested_schema")
            params["requestedSchema"] = requested_schema
        elif mode == "url":
            if not url or not elicitation_id:
                raise ValueError(
                    "url elicitation requires both url and elicitation_id"
                )
            params["url"] = url
            params["elicitationId"] = elicitation_id
        else:
            raise ValueError(
                f"invalid elicitation mode {mode!r}; expected 'form' or 'url'"
            )
        return await self._request_sender(_ELICITATION_CREATE, params)


class AgentServer:
    """An ACP agent server that speaks the protocol over stdio.

    Register handlers via the decorators (:meth:`on_prompt`,
    :meth:`on_new_session`, :meth:`on_initialize`, :meth:`on_cancel`,
    :meth:`on_session_load`), then call :meth:`run` (blocking) to serve.

    Only ``on_prompt`` is required for a useful agent; sensible defaults are
    provided for the rest.
    """

    def __init__(self, *, name: str = "conduit-agent", version: str = "0.1.0") -> None:
        self.name = name
        self.version = version
        self._handlers: dict[str, Callable[..., Any]] = {}
        self._session_mcp: dict[str, list[dict[str, Any]]] = {}
        # Pending outbound requests (agent -> client), keyed by request id.
        self._pending_requests: dict[str, asyncio.Future] = {}
        self._req_counter: int = 0

    # -- Handler registration ---------------------------------------------

    def on_initialize(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        """Override the ``initialize`` response builder.

        The handler receives ``(params: dict)`` and may return a dict of fields
        to merge into the response (e.g. extra ``agentCapabilities``).
        """
        self._handlers[_INITIALIZE] = fn
        return fn

    def on_new_session(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        """Handle ``session/new``.

        ``async def fn(params: dict) -> str`` returning the new session id
        (or a dict with ``session_id`` / ``modes`` / ``config_options``).
        """
        self._handlers[_SESSION_NEW] = fn
        return fn

    def on_session_load(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        """Handle ``session/load`` (advertises ``loadSession`` when registered)."""
        self._handlers[_SESSION_LOAD] = fn
        return fn

    def on_prompt(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        """Handle ``session/prompt`` \u2014 the core turn handler.

        ``async def fn(ctx: AgentContext, session_id: str, content: list[dict])
        -> str | None``. Stream via ``ctx.send_text(...)`` / ``ctx.send_update``;
        return the ``stop_reason`` (default ``"end_turn"``).
        """
        self._handlers[_SESSION_PROMPT] = fn
        return fn

    def on_cancel(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        """Handle the ``session/cancel`` notification (no response expected)."""
        self._handlers[_SESSION_CANCEL] = fn
        return fn

    def on_session_delete(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        """Handle ``session/delete`` \u2014 called when the client deletes a session.

        ``async def fn(params: dict) -> dict`` returning the result dict
        (typically empty on success). If no handler is registered, the default
        implementation returns an empty ``{}`` result.
        """
        self._handlers[_SESSION_DELETE] = fn
        return fn

    # -- Lifecycle --------------------------------------------------------

    def run(self) -> None:
        """Serve the agent over stdio. Blocks until stdin closes."""
        asyncio.run(self._serve())

    # -- Internals --------------------------------------------------------

    async def _serve(self) -> None:
        loop = asyncio.get_running_loop()
        write_lock = asyncio.Lock()

        async def write(msg: dict[str, Any]) -> None:
            # stdout is the ACP transport; keep writes atomic and flushed.
            async with write_lock:
                sys.stdout.write(json.dumps(msg) + "\n")
                sys.stdout.flush()

        # Read stdin line-by-line off the thread pool so the loop stays
        # responsive to in-flight prompt handlers and cancel notifications.
        while True:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(msg, dict):
                continue
            # A message carrying an id but no method is a RESPONSE to a
            # request this agent sent to the client (e.g. elicitation/create).
            if "id" in msg and "method" not in msg:
                req_id = str(msg["id"])
                fut = self._pending_requests.pop(req_id, None)
                if fut is not None and not fut.done():
                    if "error" in msg:
                        fut.set_exception(
                            RuntimeError(f"agent request error: {msg['error']}")
                        )
                    else:
                        fut.set_result(msg.get("result") or {})
                continue
            if "method" not in msg:
                continue
            asyncio.create_task(self._handle(msg, write))  # noqa: RUF006

    async def _handle(
        self,
        msg: dict[str, Any],
        write: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        method: str = msg["method"]
        req_id = msg.get("id")
        params: dict[str, Any] = msg.get("params") or {}
        try:
            if method == _INITIALIZE:
                await self._respond(req_id, await self._do_initialize(params), write)
            elif method == _SESSION_NEW:
                await self._respond(req_id, await self._do_new_session(params), write)
            elif method == _SESSION_LOAD:
                await self._respond(req_id, await self._do_session_load(params), write)
            elif method == _SESSION_PROMPT:
                await self._respond(req_id, await self._do_prompt(params, write), write)
            elif method == _SESSION_CANCEL:
                handler = self._handlers.get(_SESSION_CANCEL)
                if handler is not None:
                    await _maybe_await(handler(params))
                # notification: no response
            elif method == _SESSION_DELETE:
                handler = self._handlers.get(_SESSION_DELETE)
                if handler is not None:
                    result = await _maybe_await(handler(params))
                else:
                    result = {}
                await self._respond(req_id, result, write)
            else:
                if req_id is not None:
                    await write(
                        {
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "error": {
                                "code": -32601,
                                "message": f"method not found: {method}",
                            },
                        }
                    )
        except Exception as exc:  # noqa: BLE001
            if req_id is not None:
                await write(
                    {
                        "jsonrpc": "2.0",
                        "id": req_id,
                        "error": {
                            "code": -32603,
                            "message": f"{type(exc).__name__}: {exc}",
                        },
                    }
                )

    async def _respond(
        self,
        req_id: Any,
        result: dict[str, Any],
        write: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        if req_id is None:
            return
        await write({"jsonrpc": "2.0", "id": req_id, "result": result})

    async def _do_initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        protocol_version = params.get("protocolVersion", 1)
        capabilities: dict[str, Any] = {
            "loadSession": _SESSION_LOAD in self._handlers,
        }
        result: dict[str, Any] = {
            "protocolVersion": protocol_version,
            "agentCapabilities": capabilities,
            "agentInfo": {"name": self.name, "version": self.version},
        }
        override = self._handlers.get(_INITIALIZE)
        if override is not None:
            extra = await _maybe_await(override(params))
            if isinstance(extra, dict):
                result.update(extra)
        return result

    async def _do_new_session(self, params: dict[str, Any]) -> dict[str, Any]:
        handler = self._handlers.get(_SESSION_NEW)
        if handler is not None:
            value = await _maybe_await(handler(params))
            if isinstance(value, dict):
                if "sessionId" not in value:
                    raise ValueError("on_new_session must return a session id")
                result = value
            else:
                result = {"sessionId": str(value)}
        else:
            result = {"sessionId": uuid.uuid4().hex}
        # Capture MCP servers the client provided so on_prompt can call tools.
        self._session_mcp[result["sessionId"]] = params.get("mcpServers") or []
        return result

    async def _do_session_load(self, params: dict[str, Any]) -> dict[str, Any]:
        handler = self._handlers.get(_SESSION_LOAD)
        if handler is None:
            return {}
        value = await _maybe_await(handler(params))
        return value if isinstance(value, dict) else {}

    async def _do_prompt(
        self,
        params: dict[str, Any],
        write: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> dict[str, Any]:
        handler = self._handlers.get(_SESSION_PROMPT)
        if handler is None:
            return {"stopReason": "refusal"}
        session_id = str(params.get("sessionId", ""))
        content = params.get("prompt", []) or []
        async def send_request(
            method: str, params: dict[str, Any]
        ) -> dict[str, Any]:
            return await self._send_request(method, params, write)

        ctx = AgentContext(
            session_id,
            write,
            self._session_mcp.get(session_id, []),
            send_request,
        )
        stop = await _maybe_await(handler(ctx, session_id, content))
        if stop is None:
            stop = "end_turn"
        if stop not in _STOP_REASONS:
            raise ValueError(
                f"invalid stop_reason {stop!r}; expected one of {sorted(_STOP_REASONS)}"
            )
        return {"stopReason": stop}

    async def _send_request(
        self,
        method: str,
        params: dict[str, Any],
        write: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> dict[str, Any]:
        """Send a JSON-RPC *request* to the client and await its response.

        Registers an :class:`asyncio.Future` keyed by request id; the
        ``_serve`` read loop resolves it when the matching response arrives.
        Used by :meth:`AgentContext.request_elicitation`.
        """
        loop = asyncio.get_running_loop()
        self._req_counter += 1
        req_id = f"agent-{self._req_counter}"
        fut: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending_requests[req_id] = fut
        await write(
            {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
        )
        return await fut


async def _maybe_await(value: Any) -> Any:
    if asyncio.iscoroutine(value):
        return await value
    return value


def _mcp_call(url: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Blocking MCP ``tools/call`` over HTTP (run in an executor)."""
    payload = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }
    ).encode("utf-8")
    req = urllib.request.Request(  # noqa: S310 - agent calls a localhost SDK server
        url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        body = json.loads(resp.read())
    if "error" in body:
        raise RuntimeError(f"MCP tools/call error: {body['error']}")
    return body["result"]
