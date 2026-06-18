"""End-to-end streaming tests driving a real Client against the rich-agent fixture.

Tests that prompt_stream and prompt_sync correctly surface the full update
sequence over the real ACP wire — including the critical fix that prompt_stream
stops iterating after receiving a Done update.
"""

import os
import sys

import pytest

from conduit_sdk import Client, UpdateKind

HERE = os.path.dirname(os.path.abspath(__file__))
RICH_AGENT = [sys.executable, os.path.join(HERE, "_rich_agent_app.py")]


@pytest.mark.asyncio
async def test_prompt_stream_yields_full_update_sequence():
    """Collect all updates via prompt_stream and assert their structure and ordering.

    This test verifies the real wire protocol translation (ToolUseStart,
    ToolUseUpdate, ToolUseEnd, ThoughtDelta, TextDelta, Done) and crucially
    asserts that the async-for loop terminates — proving prompt_stream stops
    iterating after a Done update (not hanging on the next recv).
    """
    kinds: list[UpdateKind] = []
    tool_start_name: str | None = None
    tool_start_input: str | None = None
    text_text: str | None = None
    done_stop_reason: str | None = None

    async with Client(RICH_AGENT, timeout=15) as client:
        await client.new_session()
        async for update in client.prompt_stream("read config"):
            kinds.append(update.kind)
            if update.kind == UpdateKind.ToolUseStart:
                tool_start_name = update.tool_name
                tool_start_input = update.tool_input
            if update.kind == UpdateKind.TextDelta:
                text_text = update.text
            if update.kind == UpdateKind.Done:
                done_stop_reason = update.stop_reason

    # --- The async-for loop terminated (critical: guards prompt_stream-stops-on-Done fix). ---

    # Scan through kinds to verify relative order.
    thought_seen = tool_start_seen = tool_update_seen = tool_end_seen = text_seen = done_seen = False
    for k in kinds:
        if k == UpdateKind.ThoughtDelta:
            thought_seen = True
            assert not tool_start_seen, "ThoughtDelta should appear before any ToolUseStart"
        elif k == UpdateKind.ToolUseStart:
            tool_start_seen = True
            assert thought_seen, "ToolUseStart must be preceded by ThoughtDelta"
            assert not tool_update_seen, "ToolUseStart should appear before ToolUseUpdate"
        elif k == UpdateKind.ToolUseUpdate:
            tool_update_seen = True
            assert tool_start_seen, "ToolUseUpdate must be preceded by ToolUseStart"
            assert not tool_end_seen, "ToolUseUpdate should appear before ToolUseEnd"
        elif k == UpdateKind.ToolUseEnd:
            tool_end_seen = True
            assert tool_update_seen, "ToolUseEnd must be preceded by ToolUseUpdate"
            assert not text_seen, "ToolUseEnd should appear before TextDelta"
        elif k == UpdateKind.TextDelta:
            text_seen = True
            assert tool_end_seen, "TextDelta must be preceded by ToolUseEnd"
            assert not done_seen, "TextDelta should appear before Done"
        elif k == UpdateKind.Done:
            done_seen = True
            assert text_seen, "Done must be preceded by TextDelta"

    # Every expected kind was encountered.
    assert thought_seen, "Missing ThoughtDelta update"
    assert tool_start_seen, "Missing ToolUseStart update"
    assert tool_update_seen, "Missing ToolUseUpdate update"
    assert tool_end_seen, "Missing ToolUseEnd update"
    assert text_seen, "Missing TextDelta update"

    # ToolUseStart fields.
    assert tool_start_name == "Read config.txt", f"Got {tool_start_name!r}"
    assert tool_start_input is not None, "ToolUseStart missing tool_input"
    assert "config.txt" in tool_start_input

    # TextDelta carries the final agent message.
    assert text_text is not None, "TextDelta missing text"
    assert "The config sets port 8080." in text_text, f"Got {text_text!r}"

    # Done carries the correct stop reason.
    assert done_stop_reason == "EndTurn", f"Got {done_stop_reason!r}"


@pytest.mark.asyncio
async def test_prompt_sync_collects_all_text():
    """prompt_sync should return Messages whose combined text includes the agent output."""
    async with Client(RICH_AGENT, timeout=15) as client:
        await client.new_session()
        messages = await client.prompt_sync("hi")

    combined = "".join(m.text() for m in messages)
    assert "port 8080" in combined, f"Got {combined!r}"
