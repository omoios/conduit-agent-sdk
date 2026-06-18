"""Tests for conduit_sdk.events — canonical event model.

Table-driven: stub-based ``normalize``, round-trip ``to_record``/``from_record``,
totality checks, and content-decoding edge cases.
"""

from __future__ import annotations

import pytest

from conduit_sdk._conduit_sdk import UpdateKind
from conduit_sdk.events import (
    AvailableCommands,
    ConfigUpdate,
    Done,
    ModeChange,
    Plan,
    RateLimit,
    SessionInfo,
    StopReason,
    TextDelta,
    ThoughtDelta,
    ToolCallStart,
    ToolCallUpdate,
    ToolKind,
    ToolStatus,
    Unknown,
    Usage,
    _decode_tool_output,
    _safe_json,
    _to_enum,
    from_record,
    normalize,
    to_record,
)

# ---------------------------------------------------------------------------
# Stub helpers
# ---------------------------------------------------------------------------


class _U:
    """Minimal SessionUpdate faker — remember to set ``.kind``."""

    def __init__(self, **kwargs: object) -> None:
        self.kind = None
        for k, v in kwargs.items():
            setattr(self, k, v)


# ---------------------------------------------------------------------------
# normalize — one test per UpdateKind member
# ---------------------------------------------------------------------------


class TestNormalize:
    def test_text_delta(self) -> None:
        event = normalize(_U(kind=UpdateKind.TextDelta, text="hello"))
        assert isinstance(event, TextDelta)
        assert event.text == "hello"

    def test_text_delta_empty(self) -> None:
        event = normalize(_U(kind=UpdateKind.TextDelta))
        assert isinstance(event, TextDelta)
        assert event.text == ""

    def test_thought_delta(self) -> None:
        event = normalize(_U(kind=UpdateKind.ThoughtDelta, text="thinking..."))
        assert isinstance(event, ThoughtDelta)
        assert event.text == "thinking..."

    def test_tool_call_start(self) -> None:
        event = normalize(
            _U(
                kind=UpdateKind.ToolUseStart,
                tool_use_id="call_1",
                tool_name="read_file",
                tool_kind="read",
                tool_input='{"path": "/tmp/x"}',
                tool_status="pending",
            )
        )
        assert isinstance(event, ToolCallStart)
        assert event.tool_use_id == "call_1"
        assert event.title == "read_file"
        assert event.kind == ToolKind.READ
        assert event.input == {"path": "/tmp/x"}
        assert event.status == ToolStatus.PENDING

    def test_tool_call_start_raw_input(self) -> None:
        """tool_input that is not valid JSON is forwarded as a raw string."""
        event = normalize(
            _U(
                kind=UpdateKind.ToolUseStart,
                tool_use_id="call_2",
                tool_name="write",
                tool_kind="other",
                tool_input="raw text",
                tool_status="in_progress",
            )
        )
        assert isinstance(event, ToolCallStart)
        assert event.input == "raw text"
        assert event.kind == ToolKind.OTHER
        assert event.status == ToolStatus.IN_PROGRESS

    def test_tool_call_start_none_fields(self) -> None:
        """Missing optional fields produce defaults / None."""
        event = normalize(_U(kind=UpdateKind.ToolUseStart))
        assert isinstance(event, ToolCallStart)
        assert event.tool_use_id == ""
        assert event.title == ""
        assert event.kind is None
        assert event.input is None
        assert event.status is None

    def test_tool_call_update_terminal(self) -> None:
        """Terminal tool-call (completed) decodes content blocks into text."""
        content = (
            '[{"type":"content","content":{"type":"text","text":"port = 8080"}}]'
        )
        event = normalize(
            _U(
                kind=UpdateKind.ToolUseUpdate,
                tool_use_id="call_1",
                tool_status="completed",
                tool_content=content,
                tool_locations='["/tmp"]',
            )
        )
        assert isinstance(event, ToolCallUpdate)
        assert event.tool_use_id == "call_1"
        assert event.status == ToolStatus.COMPLETED
        assert event.output == "port = 8080"
        assert event.locations == ["/tmp"]

    def test_tool_call_update_non_terminal(self) -> None:
        """Non-terminal update with no text/output keys yields output=None."""
        content = '{"thinking": "working..."}'
        event = normalize(
            _U(
                kind=UpdateKind.ToolUseUpdate,
                tool_use_id="call_1",
                tool_status="in_progress",
                tool_content=content,
            )
        )
        assert isinstance(event, ToolCallUpdate)
        assert event.status == ToolStatus.IN_PROGRESS
        assert event.output is None

    def test_tool_call_update_bad_json(self) -> None:
        """Invalid tool_content JSON produces output=None and raw string."""
        event = normalize(
            _U(
                kind=UpdateKind.ToolUseUpdate,
                tool_use_id="call_1",
                tool_status="completed",
                tool_content="not valid json",
            )
        )
        assert isinstance(event, ToolCallUpdate)
        assert event.output is None
        assert event.raw_content == "not valid json"

    def test_tool_call_update_none_content(self) -> None:
        """None tool_content produces None output/raw."""
        event = normalize(
            _U(
                kind=UpdateKind.ToolUseUpdate,
                tool_use_id="call_1",
                tool_status="completed",
                tool_content=None,
            )
        )
        assert isinstance(event, ToolCallUpdate)
        assert event.output is None
        assert event.raw_content is None

    def test_tool_use_end(self) -> None:
        """Defensive arm — ToolUseEnd becomes ToolCallUpdate with all-None fields."""
        event = normalize(
            _U(kind=UpdateKind.ToolUseEnd, tool_use_id="call_1")
        )
        assert isinstance(event, ToolCallUpdate)
        assert event.tool_use_id == "call_1"
        assert event.status is None
        assert event.output is None
        assert event.raw_content is None
        assert event.locations is None

    def test_plan(self) -> None:
        event = normalize(
            _U(kind=UpdateKind.Plan, plan_json='[{"step": "do X"}]')
        )
        assert isinstance(event, Plan)
        assert event.entries == [{"step": "do X"}]

    def test_plan_empty(self) -> None:
        event = normalize(_U(kind=UpdateKind.Plan))
        assert isinstance(event, Plan)
        assert event.entries == []

    def test_available_commands(self) -> None:
        event = normalize(
            _U(
                kind=UpdateKind.CommandsUpdate,
                commands_json='[{"name": "test"}]',
            )
        )
        assert isinstance(event, AvailableCommands)
        assert event.commands == [{"name": "test"}]

    def test_mode_change(self) -> None:
        event = normalize(_U(kind=UpdateKind.ModeChange, mode_id="code"))
        assert isinstance(event, ModeChange)
        assert event.mode_id == "code"

    def test_config_update(self) -> None:
        event = normalize(
            _U(kind=UpdateKind.ConfigUpdate, config_json='{"theme": "dark"}')
        )
        assert isinstance(event, ConfigUpdate)
        assert event.config == {"theme": "dark"}

    def test_usage(self) -> None:
        event = normalize(
            _U(
                kind=UpdateKind.Usage,
                usage_json=(
                    '{"used": 100, "size": 500,'
                    ' "cost": {"amount": 0.05, "currency": "USD"}}'
                ),
            )
        )
        assert isinstance(event, Usage)
        assert event.used == 100
        assert event.size == 500
        assert event.cost_amount == 0.05
        assert event.cost_currency == "USD"

    def test_usage_invalid_json(self) -> None:
        """Malformed JSON in usage_json → Usage with None fields (no raise)."""
        event = normalize(_U(kind=UpdateKind.Usage, usage_json="{bad"))
        assert isinstance(event, Usage)
        assert event.used is None
        assert event.size is None
        assert event.cost_amount is None
        assert event.cost_currency is None

    def test_session_info(self) -> None:
        event = normalize(
            _U(
                kind=UpdateKind.SessionInfo,
                session_info_json=(
                    '{"title": "My Session", "updated_at": "2024-01-01"}'
                ),
            )
        )
        assert isinstance(event, SessionInfo)
        assert event.title == "My Session"
        assert event.updated_at == "2024-01-01"

    def test_rate_limit(self) -> None:
        event = normalize(
            _U(
                kind=UpdateKind.RateLimit,
                rate_limit_json=(
                    '{"params": {"rate_limit_info": {"status": "active",'
                    '"resetsAt": 100, "rateLimitType": "requests",'
                    '"utilization": 0.5, "isUsingOverage": false,'
                    '"surpassedThreshold": 0.0}}}'
                ),
            )
        )
        assert isinstance(event, RateLimit)
        assert event.status == "active"
        assert event.resets_at == 100
        assert event.rate_limit_type == "requests"
        assert event.utilization == 0.5
        assert event.is_using_overage is False
        assert event.surpassed_threshold == 0.0

    def test_done_with_stop_reason(self) -> None:
        """Done with a wire-format stop_reason."""
        event = normalize(
            _U(kind=UpdateKind.Done, stop_reason="end_turn")
        )
        assert isinstance(event, Done)
        assert event.stop_reason == StopReason.END_TURN

    def test_done_no_stop_reason(self) -> None:
        event = normalize(_U(kind=UpdateKind.Done))
        assert isinstance(event, Done)
        assert event.stop_reason is None

    def test_error_is_unknown(self) -> None:
        """UpdateKind.Error maps to Unknown with kind='error'."""
        event = normalize(
            _U(kind=UpdateKind.Error, error="Something went wrong")
        )
        assert isinstance(event, Unknown)
        assert event.kind == "error"
        assert event.raw == {"message": "Something went wrong"}

    def test_unknown_kind(self) -> None:
        """An unknown IntEnum value returns Unknown."""
        event = normalize(_U(kind=999))
        assert isinstance(event, Unknown)
        assert event.kind == "999"


# ---------------------------------------------------------------------------
# Round-trip: from_record(to_record(e)) == e
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_text_delta(self) -> None:
        e = TextDelta(text="hello")
        assert from_record(to_record(e)) == e

    def test_thought_delta(self) -> None:
        e = ThoughtDelta(text="thinking...")
        assert from_record(to_record(e)) == e

    def test_tool_call_start(self) -> None:
        e = ToolCallStart(
            tool_use_id="call_1",
            title="read",
            kind=ToolKind.READ,
            input={"path": "/x"},
            status=ToolStatus.PENDING,
        )
        assert from_record(to_record(e)) == e

    def test_tool_call_start_none_enums(self) -> None:
        e = ToolCallStart(
            tool_use_id="call_1",
            title="read",
            kind=None,
            input=None,
            status=None,
        )
        assert from_record(to_record(e)) == e

    def test_tool_call_update(self) -> None:
        e = ToolCallUpdate(
            tool_use_id="call_1",
            status=ToolStatus.COMPLETED,
            output="port = 8080",
            raw_content=[{"type": "text", "text": "port = 8080"}],
            locations=["/tmp"],
        )
        assert from_record(to_record(e)) == e

    def test_tool_call_update_none(self) -> None:
        e = ToolCallUpdate(
            tool_use_id="call_1",
            status=None,
            output=None,
            raw_content=None,
            locations=None,
        )
        assert from_record(to_record(e)) == e

    def test_plan(self) -> None:
        e = Plan(entries=[{"step": "do X"}])
        assert from_record(to_record(e)) == e

    def test_available_commands(self) -> None:
        e = AvailableCommands(commands=[{"name": "test"}])
        assert from_record(to_record(e)) == e

    def test_mode_change(self) -> None:
        e = ModeChange(mode_id="code")
        assert from_record(to_record(e)) == e

    def test_config_update(self) -> None:
        e = ConfigUpdate(config={"theme": "dark"})
        assert from_record(to_record(e)) == e

    def test_usage(self) -> None:
        e = Usage(
            used=100,
            size=500,
            cost_amount=0.05,
            cost_currency="USD",
        )
        assert from_record(to_record(e)) == e

    def test_usage_none(self) -> None:
        e = Usage(
            used=None,
            size=None,
            cost_amount=None,
            cost_currency=None,
        )
        assert from_record(to_record(e)) == e

    def test_session_info(self) -> None:
        e = SessionInfo(title="Session", updated_at="2024-01-01")
        assert from_record(to_record(e)) == e

    def test_session_info_none(self) -> None:
        e = SessionInfo(title=None, updated_at=None)
        assert from_record(to_record(e)) == e

    def test_rate_limit(self) -> None:
        e = RateLimit(
            status="active",
            resets_at=100,
            rate_limit_type="requests",
            utilization=0.5,
            is_using_overage=False,
            surpassed_threshold=0.0,
        )
        assert from_record(to_record(e)) == e

    def test_done(self) -> None:
        e = Done(stop_reason=StopReason.END_TURN)
        assert from_record(to_record(e)) == e

    def test_done_none(self) -> None:
        e = Done(stop_reason=None)
        assert from_record(to_record(e)) == e

    def test_unknown(self) -> None:
        e = Unknown(kind="error", raw={"message": "test"})
        assert from_record(to_record(e)) == e


# ---------------------------------------------------------------------------
# Totality / non-raising
# ---------------------------------------------------------------------------


class TestTotality:
    def test_unknown_kind_does_not_raise(self) -> None:
        """An int not matching any UpdateKind returns Unknown."""
        event = normalize(_U(kind=999))
        assert isinstance(event, Unknown)

    def test_malformed_usage_json_does_not_raise(self) -> None:
        """Malformed usage_json produces Usage with None fields."""
        event = normalize(_U(kind=UpdateKind.Usage, usage_json="{bad"))
        assert isinstance(event, Usage)
        assert event.used is None

    def test_malformed_tool_content_does_not_raise(self) -> None:
        """Malformed tool_content produces output=None, raw string."""
        event = normalize(
            _U(
                kind=UpdateKind.ToolUseUpdate,
                tool_use_id="call_1",
                tool_status="completed",
                tool_content="[not json",
            )
        )
        assert isinstance(event, ToolCallUpdate)
        assert event.output is None
        assert event.raw_content == "[not json"

    def test_missing_kind_does_not_raise(self) -> None:
        """An object without .kind yields normalize_error Unknown."""
        event = normalize(object())  # type: ignore[arg-type]
        assert isinstance(event, Unknown)
        assert event.kind == "normalize_error"


# ---------------------------------------------------------------------------
# _decode_tool_output
# ---------------------------------------------------------------------------


class TestDecodeToolOutput:
    def test_rich_agent_content(self) -> None:
        """Real ACP tool_content format yields joined text."""
        output, raw = _decode_tool_output(
            '[{"type":"content","content":{"type":"text","text":"port = 8080"}}]'
        )
        assert output == "port = 8080"
        assert raw == [
            {"type": "content", "content": {"type": "text", "text": "port = 8080"}}
        ]

    def test_none(self) -> None:
        output, raw = _decode_tool_output(None)
        assert output is None
        assert raw is None

    def test_empty(self) -> None:
        output, raw = _decode_tool_output("")
        assert output is None
        assert raw is None


# ---------------------------------------------------------------------------
# _safe_json
# ---------------------------------------------------------------------------


class TestSafeJson:
    def test_valid(self) -> None:
        assert _safe_json('{"a": 1}') == {"a": 1}

    def test_invalid(self) -> None:
        assert _safe_json("not json") == "not json"

    def test_none(self) -> None:
        assert _safe_json(None) is None

    def test_empty(self) -> None:
        assert _safe_json("") is None


# ---------------------------------------------------------------------------
# _to_enum
# ---------------------------------------------------------------------------


class TestToEnum:
    def test_valid(self) -> None:
        assert _to_enum(ToolKind, "read", None) == ToolKind.READ
        assert _to_enum(ToolStatus, "completed", None) == ToolStatus.COMPLETED
        assert _to_enum(StopReason, "end_turn", None) == StopReason.END_TURN

    def test_invalid(self) -> None:
        assert _to_enum(ToolKind, "no_such_kind", ToolKind.OTHER) == ToolKind.OTHER
        assert _to_enum(ToolStatus, "maybe", None) is None

    def test_none(self) -> None:
        assert _to_enum(ToolKind, None, None) is None
        assert _to_enum(ToolKind, None, ToolKind.OTHER) == ToolKind.OTHER


# ---------------------------------------------------------------------------
# from_record — unknown discriminator
# ---------------------------------------------------------------------------


class TestFromRecordUnknown:
    def test_unknown_discriminator(self) -> None:
        result = from_record({"event": "bogus", "foo": "bar"})
        assert isinstance(result, Unknown)
        assert result.kind == "bogus"
        assert result.raw == {"event": "bogus", "foo": "bar"}
