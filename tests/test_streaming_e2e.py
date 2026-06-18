"""End-to-end streaming tests driving a real Client against the rich-agent fixture.

Tests that prompt_stream and prompt_sync correctly surface the full update
sequence over the real ACP wire — including the critical fix that prompt_stream
stops iterating after receiving a Done update.
"""

import os
import sys

import pytest

from conduit_sdk import Client
from conduit_sdk.events import (
    Done,
    StopReason,
    TextDelta,
    ThoughtDelta,
    ToolCallStart,
    ToolCallUpdate,
    ToolKind,
    ToolStatus,
)

HERE = os.path.dirname(os.path.abspath(__file__))
RICH_AGENT = [sys.executable, os.path.join(HERE, "_rich_agent_app.py")]


@pytest.mark.asyncio
async def test_prompt_stream_yields_full_update_sequence():
    """Collect the canonical SessionEvent stream and assert structure + ordering.

    Verifies the real wire decode (ThoughtDelta, ToolCallStart, terminal
    ToolCallUpdate, TextDelta, Done) and that the async-for loop terminates
    after Done — proving prompt_stream stops iterating (not hanging on recv).
    """
    types: list[str] = []
    tool_start_name: str | None = None
    tool_start_input: object = None
    tool_start_kind: object = None
    tool_update_status: object = None
    text_text: str | None = None
    done_stop_reason: object = None

    async with Client(RICH_AGENT, timeout=15) as client:
        await client.new_session()
        async for event in client.prompt_stream("read config"):
            types.append(type(event).__name__)
            if isinstance(event, ToolCallStart):
                tool_start_name = event.title
                tool_start_input = event.input
                tool_start_kind = event.kind
            elif isinstance(event, ToolCallUpdate):
                tool_update_status = event.status
            elif isinstance(event, TextDelta):
                text_text = event.text
            elif isinstance(event, Done):
                done_stop_reason = event.stop_reason

    # --- The async-for loop terminated (critical: guards prompt_stream-stops-on-Done fix). ---

    # Scan through event types to verify relative order.
    thought_seen = tool_start_seen = tool_update_seen = text_seen = done_seen = False
    for t in types:
        if t == "ThoughtDelta":
            thought_seen = True
            assert not tool_start_seen, "ThoughtDelta should appear before ToolCallStart"
        elif t == "ToolCallStart":
            tool_start_seen = True
            assert thought_seen, "ToolCallStart must be preceded by ThoughtDelta"
            assert not tool_update_seen, "ToolCallStart should appear before ToolCallUpdate"
        elif t == "ToolCallUpdate":
            tool_update_seen = True
            assert tool_start_seen, "ToolCallUpdate must be preceded by ToolCallStart"
            assert not text_seen, "ToolCallUpdate should appear before TextDelta"
        elif t == "TextDelta":
            text_seen = True
            assert tool_update_seen, "TextDelta must be preceded by the terminal ToolCallUpdate"
            assert not done_seen, "TextDelta should appear before Done"
        elif t == "Done":
            done_seen = True
            assert text_seen, "Done must be preceded by TextDelta"

    # Every expected event was encountered.
    assert thought_seen, "Missing ThoughtDelta event"
    assert tool_start_seen, "Missing ToolCallStart event"
    assert tool_update_seen, "Missing ToolCallUpdate event"
    assert text_seen, "Missing TextDelta event"
    assert done_seen, "Missing Done event"

    # ToolCallStart fields. Enum fields are typed (ToolKind/ToolStatus/StopReason).
    assert tool_start_name == "Read config.txt", f"Got {tool_start_name!r}"
    assert tool_start_input is not None, "ToolCallStart missing input"
    assert "config.txt" in str(tool_start_input)
    assert tool_start_kind == ToolKind.READ, f"Got {tool_start_kind!r}"
    assert tool_update_status == ToolStatus.COMPLETED, f"Got {tool_update_status!r}"

    # TextDelta carries the final agent message.
    assert text_text is not None, "TextDelta missing text"
    assert "The config sets port 8080." in text_text, f"Got {text_text!r}"

    # Done carries the typed stop reason.
    assert done_stop_reason == StopReason.END_TURN, f"Got {done_stop_reason!r}"


@pytest.mark.asyncio
async def test_prompt_sync_collects_all_text():
    """prompt_sync should return Messages whose combined text includes the agent output."""
    async with Client(RICH_AGENT, timeout=15) as client:
        await client.new_session()
        messages = await client.prompt_sync("hi")

    combined = "".join(m.text() for m in messages)
    assert "port 8080" in combined, f"Got {combined!r}"
