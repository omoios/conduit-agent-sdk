"""Loopback tests for AgentContext typed emit helpers (tool_call, tool_result,
plan, usage, mode_change).

Drives a real Client against the emit-agent fixture and asserts the stream
of SessionUpdate objects matches what each helper should produce.
"""

from __future__ import annotations

import os
import sys

import pytest

from conduit_sdk import Client
from conduit_sdk.events import (
    Done,
    ModeChange,
    Plan,
    StopReason,
    TextDelta,
    ToolCallStart,
    ToolCallUpdate,
    ToolKind,
    ToolStatus,
    Usage,
)

HERE = os.path.dirname(os.path.abspath(__file__))
EMIT_AGENT = [sys.executable, os.path.join(HERE, "_emit_agent_app.py")]


@pytest.mark.asyncio
async def test_emit_helpers_surface_correct_events() -> None:
    """Every emit helper should produce a matching SessionEvent in the stream."""
    seen: dict[str, bool] = {}
    tool_start_name: str | None = None
    tool_start_kind: object = None
    tool_start_input: object = None
    tool_update_status: object = None
    tool_update_output: str | None = None
    text_text: str | None = None
    done_stop_reason: object = None
    mode_id_found: str | None = None
    plan_entries: object = None
    usage_used: object = None
    usage_size: object = None

    async with Client(EMIT_AGENT, timeout=15) as client:
        await client.new_session()
        async for event in client.prompt_stream("go"):
            seen[type(event).__name__] = True
            if isinstance(event, ToolCallStart):
                tool_start_name = event.title
                tool_start_kind = event.kind
                tool_start_input = event.input
            elif isinstance(event, ToolCallUpdate):
                tool_update_status = event.status
                tool_update_output = event.output
            elif isinstance(event, TextDelta):
                text_text = event.text
            elif isinstance(event, Done):
                done_stop_reason = event.stop_reason
            elif isinstance(event, ModeChange):
                mode_id_found = event.mode_id
            elif isinstance(event, Plan):
                plan_entries = event.entries
            elif isinstance(event, Usage):
                usage_used = event.used
                usage_size = event.size

    # ── every expected event type was encountered ──
    for name in (
        "ToolCallStart", "ToolCallUpdate", "Plan", "Usage",
        "ModeChange", "TextDelta", "Done",
    ):
        assert seen.get(name), f"Missing {name} event"

    # ── ToolCallStart ──
    assert tool_start_name == "Search", f"Got {tool_start_name!r}"
    assert tool_start_kind == ToolKind.SEARCH, f"Got {tool_start_kind!r}"
    assert tool_start_input == {"q": "x"}, f"Got {tool_start_input!r}"

    # ── ToolCallUpdate (terminal result from tool_result) ──
    assert tool_update_status == ToolStatus.COMPLETED, f"Got {tool_update_status!r}"
    assert tool_update_output == "found 3 hits", f"Got {tool_update_output!r}"

    # ── Plan ──
    assert plan_entries is not None
    assert "g1" in str(plan_entries)

    # ── Usage ──
    assert usage_used == 100, f"Got {usage_used!r}"
    assert usage_size == 200, f"Got {usage_size!r}"

    # ── ModeChange ──
    assert mode_id_found == "build", f"Got {mode_id_found!r}"

    # ── TextDelta ──
    assert text_text == "done", f"Got {text_text!r}"

    # ── Done ──
    assert done_stop_reason == StopReason.END_TURN, f"Got {done_stop_reason!r}"
