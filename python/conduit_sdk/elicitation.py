"""Elicitation support for conduit-agent-sdk (**UNSTABLE**).

Elicitation lets an ACP agent request *structured user input* from the client
via the ``elicitation/create`` JSON-RPC method (agent -> client request). The
client — this SDK when driving an agent — receives the request and routes it
to a user-supplied handler that returns the user's decision
(accept / decline / cancel) plus any content.

This maps onto the ACP capability
``ClientCapabilities.elicitation = {form, url}``, which the Rust core
advertises during the ``initialize`` handshake when a handler is configured.

.. warning::

   Elicitation is **UNSTABLE** in ACP and may change or be removed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable

__all__ = [
    "ElicitationMode",
    "ElicitationAction",
    "ElicitationRequest",
    "ElicitationResponse",
    "ElicitationHandler",
    "auto_decline",
    "auto_accept",
    "console_elicit",
]


class ElicitationMode(str, Enum):
    """How the client should collect input."""

    FORM = "form"
    URL = "url"


class ElicitationAction(str, Enum):
    """The user's decision in response to an elicitation."""

    ACCEPT = "accept"
    DECLINE = "decline"
    CANCEL = "cancel"


@dataclass
class ElicitationRequest:
    """An elicitation request received from the agent.

    Attributes
    ----------
    message:
        Human-readable description of what input is needed.
    mode:
        ``"form"`` (render a form) or ``"url"`` (direct user to a URL).
    requested_schema:
        JSON Schema object describing the form fields (form mode only).
    url:
        The URL the user must visit (url mode only).
    elicitation_id:
        Unique id of a url-mode elicitation (url mode only).
    session_id:
        Session this elicitation is tied to (when session-scoped).
    tool_call_id:
        Optional tool call within the session that triggered the elicitation.
    """

    message: str
    mode: str = "form"
    requested_schema: dict[str, Any] | None = None
    url: str | None = None
    elicitation_id: str | None = None
    session_id: str | None = None
    tool_call_id: str | None = None


@dataclass
class ElicitationResponse:
    """The client's response to an elicitation request.

    Attributes
    ----------
    action:
        One of ``"accept"``, ``"decline"``, ``"cancel"``.
    content:
        The user-provided values, matching ``requested_schema``. Only
        meaningful for ``accept``; ignored otherwise.
    """

    action: str = "decline"
    content: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.action not in ("accept", "decline", "cancel"):
            raise ValueError(
                f"invalid elicitation action {self.action!r}; "
                "expected 'accept', 'decline', or 'cancel'"
            )
        if self.action != "accept":
            # Content is only valid on accept; drop it otherwise so the wire
            # payload matches the ACP spec.
            self.content = None


#: Signature of a user-supplied elicitation handler.
ElicitationHandler = Callable[[ElicitationRequest], Awaitable[ElicitationResponse]]


# ---------------------------------------------------------------------------
# Built-in handlers
# ---------------------------------------------------------------------------


async def auto_decline(request: ElicitationRequest) -> ElicitationResponse:
    """Always decline the elicitation."""
    return ElicitationResponse(action="decline")


async def auto_accept(request: ElicitationRequest) -> ElicitationResponse:
    """Accept the elicitation with empty content."""
    return ElicitationResponse(action="accept", content={})


async def console_elicit(request: ElicitationRequest) -> ElicitationResponse:
    """Prompt the user in the terminal based on the requested schema.

    For ``form`` mode, reads a value for each property in
    ``requested_schema`` (coercing to the declared type). Required fields that
    are left blank cancel the elicitation.

    For ``url`` mode, prints the URL and returns ``cancel`` — URL elicitation
    results are delivered out of band (via ``elicitation/complete``), so the
    synchronous request cannot capture them.
    """
    print(f"\n--- Elicitation ({request.mode}) ---")
    print(request.message)

    if request.mode == "url":
        if request.url:
            print(f"URL: {request.url}")
        print("(URL elicitation results are delivered out of band.)")
        return ElicitationResponse(action="cancel")

    schema = request.requested_schema or {}
    props: dict[str, Any] = schema.get("properties", {})
    required = set(schema.get("required", []))
    content: dict[str, Any] = {}

    for name, spec in props.items():
        label = spec.get("title", name)
        suffix = "" if name in required else " (optional, blank to skip)"
        raw = input(f"{label}{suffix}: ").strip()
        if not raw:
            if name in required:
                return ElicitationResponse(action="cancel")
            continue
        ptype = spec.get("type", "string")
        if ptype == "integer":
            try:
                content[name] = int(raw)
            except ValueError:
                return ElicitationResponse(action="cancel")
        elif ptype == "number":
            try:
                content[name] = float(raw)
            except ValueError:
                return ElicitationResponse(action="cancel")
        elif ptype == "boolean":
            content[name] = raw.lower() in ("y", "yes", "true", "1")
        else:
            content[name] = raw

    return ElicitationResponse(action="accept", content=content)


# ---------------------------------------------------------------------------
# Rust bridge adapter
# ---------------------------------------------------------------------------


def _make_elicitation_bridge(
    handler: ElicitationHandler,
) -> Callable[[str], Awaitable[str]]:
    """Wrap a user handler into the ``(json_str -> json_str)`` callback the
    Rust core invokes when an ``elicitation/create`` request arrives.

    The bridge deserializes the ACP request payload into an
    :class:`ElicitationRequest`, calls the user handler, and serializes the
    resulting :class:`ElicitationResponse` back to JSON.
    """

    async def bridge(payload_json: str) -> str:
        data: dict[str, Any] = json.loads(payload_json) if payload_json else {}
        request = ElicitationRequest(
            message=data.get("message", ""),
            mode=data.get("mode", "form"),
            requested_schema=data.get("requestedSchema")
            or data.get("requested_schema"),
            url=data.get("url"),
            elicitation_id=data.get("elicitationId")
            or data.get("elicitation_id"),
            session_id=data.get("sessionId") or data.get("session_id"),
            tool_call_id=data.get("toolCallId") or data.get("tool_call_id"),
        )
        result = await handler(request)
        if isinstance(result, ElicitationResponse):
            payload = {"action": result.action, "content": result.content}
        elif isinstance(result, dict):
            payload = {
                "action": result.get("action", "decline"),
                "content": result.get("content"),
            }
        else:
            raise TypeError(
                "elicitation handler must return ElicitationResponse or dict, "
                f"got {type(result).__name__}"
            )
        return json.dumps(payload)

    return bridge
