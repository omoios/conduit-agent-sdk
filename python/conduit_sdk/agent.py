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
    ) -> None:
        self.session_id = session_id
        self._write = write

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
            if not isinstance(msg, dict) or "method" not in msg:
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
                return value
            return {"sessionId": str(value)}
        return {"sessionId": uuid.uuid4().hex}

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
        ctx = AgentContext(session_id, write)
        stop = await _maybe_await(handler(ctx, session_id, content))
        if stop is None:
            stop = "end_turn"
        if stop not in _STOP_REASONS:
            raise ValueError(
                f"invalid stop_reason {stop!r}; expected one of {sorted(_STOP_REASONS)}"
            )
        return {"stopReason": stop}


async def _maybe_await(value: Any) -> Any:
    if asyncio.iscoroutine(value):
        return await value
    return value
