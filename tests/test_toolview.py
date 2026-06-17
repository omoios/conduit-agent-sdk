"""Tests for conduit_sdk.toolview — tool-call output observability."""

from __future__ import annotations

import pytest

from conduit_sdk import SessionUpdate, UpdateKind
from conduit_sdk.toolview import (
    TurnResult,
    collect_tool_calls,
    observe_turn,
    parse_tool_input,
    tool_output_text,
)


# --- tool_output_text --------------------------------------------------------


class TestToolOutputText:
    def test_nested_content_blocks_joined(self):
        # Real shape from omp acp: list of {type:content, content:{type:text, text}}
        content = '[{"type":"content","content":{"type":"text","text":"$ pwd"}},' \
                  '{"type":"content","content":{"type":"text","text":"/home/u\\n"}}]'
        assert tool_output_text(content) == "$ pwd\n/home/u\n"

    def test_ignores_type_discriminant(self):
        # The literal "text"/"content" type values must NOT leak into output.
        content = '{"type":"content","content":{"type":"text","text":"hello"}}'
        assert tool_output_text(content) == "hello"

    def test_output_key_also_collected(self):
        content = '[{"content":{"type":"output","output":"done"}}]'
        assert tool_output_text(content) == "done"

    def test_none_and_empty(self):
        assert tool_output_text(None) is None
        assert tool_output_text("") is None

    def test_malformed_returns_raw(self):
        assert tool_output_text("not json") == "not json"

    def test_empty_list_yields_none(self):
        assert tool_output_text("[]") is None


# --- parse_tool_input --------------------------------------------------------


class TestParseToolInput:
    def test_json_dict(self):
        assert parse_tool_input('{"path":"x.toml"}') == {"path": "x.toml"}

    def test_none_and_empty(self):
        assert parse_tool_input(None) is None
        assert parse_tool_input("") is None

    def test_non_json_passthrough(self):
        assert parse_tool_input("raw") == "raw"


# --- collect_tool_calls ------------------------------------------------------


def _content(text: str) -> str:
    import json
    return json.dumps([{"type": "content", "content": {"type": "text", "text": text}}])


class TestCollectToolCalls:
    def test_groups_two_tools_by_id(self):
        updates = [
            SessionUpdate(UpdateKind.ToolUseStart, tool_use_id="t1",
                          tool_name="read_file", tool_kind="Read",
                          tool_input='{"path":"a.txt"}', tool_status="Pending"),
            SessionUpdate(UpdateKind.ToolUseStart, tool_use_id="t2",
                          tool_name="$ ls", tool_kind="Bash",
                          tool_input='{"command":"ls"}', tool_status="Pending"),
            SessionUpdate(UpdateKind.ToolUseUpdate, tool_use_id="t1",
                          tool_content=_content("contents of a"), tool_status="Completed"),
            SessionUpdate(UpdateKind.ToolUseUpdate, tool_use_id="t2",
                          tool_content=_content("file1\nfile2"), tool_status="Completed"),
            SessionUpdate(UpdateKind.ToolUseEnd, tool_use_id="t1", tool_status="Completed"),
            SessionUpdate(UpdateKind.ToolUseEnd, tool_use_id="t2", tool_status="Completed"),
            SessionUpdate(UpdateKind.TextDelta, text="all done"),
        ]
        text, calls = collect_tool_calls(updates)

        assert text == "all done"
        assert len(calls) == 2
        by_id = {c.tool_use_id: c for c in calls}
        assert by_id["t1"].name == "read_file"
        assert by_id["t1"].input == {"path": "a.txt"}
        assert by_id["t1"].output == "contents of a"
        assert by_id["t1"].status == "Completed"
        assert by_id["t2"].name == "$ ls"
        assert by_id["t2"].output == "file1\nfile2"

    def test_update_without_start_still_grouped(self):
        # An update arriving with no prior Start (defensive) still produces a call.
        updates = [
            SessionUpdate(UpdateKind.ToolUseUpdate, tool_use_id="x",
                          tool_content=_content("late output"), tool_status="Completed"),
        ]
        _, calls = collect_tool_calls(updates)
        assert len(calls) == 1
        assert calls[0].output == "late output"

    def test_interleaved_text_and_tools_preserve_text_order(self):
        updates = [
            SessionUpdate(UpdateKind.TextDelta, text="A"),
            SessionUpdate(UpdateKind.ToolUseStart, tool_use_id="t", tool_name="r"),
            SessionUpdate(UpdateKind.TextDelta, text="B"),
        ]
        text, calls = collect_tool_calls(updates)
        assert text == "AB"
        assert len(calls) == 1


# --- observe_turn (via a fake client) ---------------------------------------


class _FakeClient:
    def __init__(self, updates):
        self._updates = updates
        self.prompted = None

    async def prompt_stream(self, text, *, session_id=None):
        self.prompted = text
        for u in self._updates:
            yield u


@pytest.mark.asyncio
async def test_observe_turn_returns_text_and_tool_calls():
    updates = [
        SessionUpdate(UpdateKind.ToolUseStart, tool_use_id="t1", tool_name="read_file",
                      tool_input='{"path":"m.toml"}'),
        SessionUpdate(UpdateKind.ToolUseUpdate, tool_use_id="t1",
                      tool_content=_content("name = 'x'"), tool_status="Completed"),
        SessionUpdate(UpdateKind.ToolUseEnd, tool_use_id="t1", tool_status="Completed"),
        SessionUpdate(UpdateKind.TextDelta, text="done"),
    ]
    client = _FakeClient(updates)
    turn = await observe_turn(client, "read it", session_id="s1")

    assert isinstance(turn, TurnResult)
    assert client.prompted == "read it"
    assert turn.text == "done"
    assert len(turn.tool_calls) == 1
    assert turn.tool_calls[0].name == "read_file"
    assert turn.tool_calls[0].output == "name = 'x'"
    assert turn.tool_calls[0].input == {"path": "m.toml"}
