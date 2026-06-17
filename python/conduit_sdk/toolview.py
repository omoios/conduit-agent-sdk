"""Observability for agent tool calls — see *what* the agent did and its *outputs*.

Agents handle file management and terminal management via their OWN tools (the
SDK does not host fs/terminal locally). Those tool calls and their results flow
through the ACP ``session/update`` stream as :class:`SessionUpdate` events of
kind ``ToolUseStart`` / ``ToolUseUpdate`` / ``ToolUseEnd``. The output itself
arrives in ``SessionUpdate.tool_content`` as a *nested* JSON string of content
blocks, which is awkward to read.

This module turns that raw stream into a clean, human-readable view:

- :func:`tool_output_text` parses the nested ``tool_content`` JSON into plain text.
- :func:`collect_tool_calls` groups a turn's updates into :class:`ToolCall`
  records (name, parsed input, parsed output, status) keyed by ``tool_use_id``.
- :func:`observe_turn` runs a prompt turn and returns both the final text and the
  structured tool calls — the easy way to *see* file/terminal tool outputs.

Example::

    from conduit_sdk import Client
    from conduit_sdk.toolview import observe_turn

    async with Client(["omp", "acp"]) as client:
        await client.new_session()
        turn = await observe_turn(client, "Read pyproject.toml and run `pwd`.")
        print(turn.text)
        for tc in turn.tool_calls:
            print(f"  {tc.name}  ->  {tc.output}")
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from conduit_sdk._conduit_sdk import UpdateKind

__all__ = [
    "ToolCall",
    "TurnResult",
    "tool_output_text",
    "parse_tool_input",
    "collect_tool_calls",
    "observe_turn",
]


@dataclass
class ToolCall:
    """One tool invocation within a turn, with its output decoded to text."""

    tool_use_id: str | None = None
    name: str | None = None
    input: Any = None
    output: str | None = None
    status: str | None = None
    kind: str | None = None


@dataclass
class TurnResult:
    """The observable outcome of a prompt turn: final text + tool calls."""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)


def _extract_text(node: Any, _key: str | None = None) -> list[str]:
    """Collect string leaves that live under a ``text``/``output`` key.

    ACP content blocks look like ``{"type": "content", "content": {"type":
    "text", "text": "..."}}`` — only the payload under ``text``/``output``
    is real output; ``type`` discriminants must be ignored.
    """
    out: list[str] = []
    if isinstance(node, str):
        if _key in ("text", "output"):
            out.append(node)
    elif isinstance(node, dict):
        for k, v in node.items():
            out.extend(_extract_text(v, _key=k))
    elif isinstance(node, (list, tuple)):
        for item in node:
            out.extend(_extract_text(item))
    return out


def tool_output_text(content_json: str | None) -> str | None:
    """Parse a ``tool_content`` JSON string (a list of content blocks) into text.

    Returns the joined payload text (``content.text`` / ``content.output``
    leaves), or ``None`` when there is nothing to show. Malformed/empty input
    yields ``None`` rather than raising.
    """
    if not content_json:
        return None
    try:
        parsed = json.loads(content_json)
    except (json.JSONDecodeError, TypeError):
        return content_json
    parts = _extract_text(parsed)
    return "\n".join(parts) if parts else None


def parse_tool_input(tool_input: str | None) -> Any:
    """Parse a ``tool_input`` JSON string into a dict (or return raw/None)."""
    if not tool_input:
        return None
    try:
        return json.loads(tool_input)
    except (json.JSONDecodeError, TypeError):
        return tool_input


def _fallback_id(index: int) -> str:
    """Synthesize an id when an update lacks tool_use_id (rare)."""
    return f"__tool_{index}"


def collect_tool_calls(updates: Iterable[Any]) -> tuple[str, list[ToolCall]]:
    """Group a turn's :class:`SessionUpdate` stream into ``(text, tool_calls)``.

    Tool events are matched by ``tool_use_id``: a ``ToolUseStart`` opens a
    :class:`ToolCall` (name/input/kind), subsequent ``ToolUseUpdate`` events
    fill in the parsed output + status, and ``ToolUseEnd`` finalizes it.
    ``TextDelta`` events are concatenated into the returned text.
    """
    text_parts: list[str] = []
    calls: list[ToolCall] = []
    by_id: dict[str, ToolCall] = {}

    def _bucket(u: Any) -> ToolCall:
        tid = getattr(u, "tool_use_id", None) or _fallback_id(len(calls))
        tc = by_id.get(tid)
        if tc is None:
            tc = ToolCall(tool_use_id=tid)
            by_id[tid] = tc
            calls.append(tc)
        return tc

    for u in updates:
        kind = getattr(u, "kind", None)
        if kind == UpdateKind.TextDelta:
            t = getattr(u, "text", None)
            if t:
                text_parts.append(t)
        elif kind == UpdateKind.ToolUseStart:
            tc = _bucket(u)
            tc.name = getattr(u, "tool_name", None) or tc.name
            tc.kind = getattr(u, "tool_kind", None) or tc.kind
            tc.status = getattr(u, "tool_status", None) or tc.status
            if getattr(u, "tool_input", None):
                tc.input = parse_tool_input(u.tool_input)
            if getattr(u, "tool_content", None):
                decoded = tool_output_text(u.tool_content)
                if decoded:
                    tc.output = decoded
        elif kind == UpdateKind.ToolUseUpdate:
            tc = _bucket(u)
            if getattr(u, "tool_status", None):
                tc.status = u.tool_status
            if getattr(u, "tool_content", None):
                decoded = tool_output_text(u.tool_content)
                if decoded:
                    # Updates may arrive incrementally; keep the richest seen.
                    tc.output = decoded
        elif kind == UpdateKind.ToolUseEnd:
            tc = _bucket(u)
            if getattr(u, "tool_status", None):
                tc.status = u.tool_status

    return "".join(text_parts), calls


async def observe_turn(
    client: Any,
    text: str,
    *,
    session_id: str | None = None,
) -> TurnResult:
    """Run a prompt turn and return its final text plus structured tool calls.

    This is the convenience entry point for *seeing* file/terminal tool outputs:
    it consumes ``client.prompt_stream(text, session_id=...)`` and decodes every
    tool call's output into readable text.
    """
    updates = [u async for u in client.prompt_stream(text, session_id=session_id)]
    body, tool_calls = collect_tool_calls(updates)
    return TurnResult(text=body, tool_calls=tool_calls)
