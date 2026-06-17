"""Tests for conduit_sdk.runlayer — Run layer.

Covers mock-run lifecycle, event schema validation, failure semantics,
and ACP normalization without a live agent.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest

from conduit_sdk.runlayer import (
    Agent,
    AgentEvent,
    Result,
    Runner,
    acp_adapter,
    mock_adapter,
    _VALID_SOURCES,
    _VALID_REDACTIONS,
    _VALID_STATUSES,
)


# Import UpdateKind for ACP stub objects
from conduit_sdk._conduit_sdk import UpdateKind


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _StubUpdate:
    """Minimal stand-in for a ``SessionUpdate`` from the Rust core.

    Only the attributes the ACP adapter reads are populated.
    """

    def __init__(self, kind: UpdateKind, **overrides: object) -> None:
        self.kind = kind
        self.text: str | None = None
        self.tool_name: str | None = None
        self.tool_use_id: str | None = None
        self.tool_input: str | None = None
        self.tool_content: str | None = None
        self.tool_status: str | None = None
        self.error: str | None = None
        self.usage_json: str | None = None
        self.stop_reason: str | None = None
        self.plan_json: str | None = None
        self.config_json: str | None = None
        self.commands_json: str | None = None
        self.session_info_json: str | None = None
        self.rate_limit_json: str | None = None
        self.tool_kind: str | None = None
        self.tool_locations: str | None = None
        self.mode_id: str | None = None
        for k, v in overrides.items():
            setattr(self, k, v)


class _StubClient:
    """A mock client that yields pre-built stub updates from ``prompt_stream``."""

    def __init__(self, updates: list[_StubUpdate]) -> None:
        self._updates = updates

    async def prompt_stream(
        self, text: str, *, session_id: str | None = None
    ) -> AsyncIterator[_StubUpdate]:
        for u in self._updates:
            yield u


# ---------------------------------------------------------------------------
# Mock-run lifecycle
# ---------------------------------------------------------------------------


class TestMockRun:
    """Runner.start + mock_adapter → Run.events + Run.result."""

    @pytest.mark.asyncio
    async def test_mock_run_lifecycle(self) -> None:
        """Basic happy path: iterate events, verify sequence and contents."""
        agent = Agent(name="test-agent")
        adapter = mock_adapter([
            ("agent.message.delta", {"text": "hello"}),
        ])
        run = await Runner.start(agent, task="say hi", adapter=adapter)

        events: list[AgentEvent] = []
        async for ev in run.events():
            events.append(ev)

        assert len(events) >= 2  # run.started + run.completed + the delta
        # Sequence starts at 1 and is strictly increasing
        for i, ev in enumerate(events, start=1):
            assert ev.sequence == i, f"expected seq {i}, got {ev.sequence}"

        types = {ev.type for ev in events}
        assert "run.started" in types
        assert "run.completed" in types
        assert "agent.message.delta" in types

        result = await run.result()
        assert result.status == "completed"
        assert result.event_count == len(events)

    @pytest.mark.asyncio
    async def test_empty_script_adds_lifecycle(self) -> None:
        """An empty script still yields run.started and run.completed."""
        agent = Agent(name="x")
        adapter = mock_adapter([])
        run = await Runner.start(agent, task="hi", adapter=adapter)

        events: list[AgentEvent] = []
        async for ev in run.events():
            events.append(ev)

        assert len(events) == 2
        assert events[0].type == "run.started"
        assert events[1].type == "run.completed"

        result = await run.result()
        assert result.status == "completed"
        assert result.event_count == 2

    @pytest.mark.asyncio
    async def test_result_without_iterating_events(self) -> None:
        """Calling result() directly (without events()) still works."""
        agent = Agent(name="x")
        adapter = mock_adapter([
            ("agent.message.delta", {"text": "hello"}),
        ])
        run = await Runner.start(agent, task="hi", adapter=adapter)

        result = await run.result()
        assert result.status == "completed"
        assert result.event_count == 3  # started + delta + completed

    @pytest.mark.asyncio
    async def test_result_called_twice_is_idempotent(self) -> None:
        """Calling result() multiple times returns the cached value."""
        agent = Agent(name="x")
        adapter = mock_adapter([("agent.message.delta", {"text": "hi"})])
        run = await Runner.start(agent, task="hi", adapter=adapter)

        r1 = await run.result()
        r2 = await run.result()
        assert r1.status == r2.status
        assert r1.event_count == r2.event_count
        assert r1.run_id == r2.run_id
        assert r1.ended_at == r2.ended_at  # same cached timestamp


# ---------------------------------------------------------------------------
# Event schema validation
# ---------------------------------------------------------------------------


class TestEventSchema:
    """Every emitted AgentEvent must satisfy the envelope contract."""

    async def _collect_events(self, script: list) -> list[AgentEvent]:
        agent = Agent(name="validator")
        adapter = mock_adapter(script)
        run = await Runner.start(agent, task="test", adapter=adapter)
        return [ev async for ev in run.events()]

    @pytest.mark.asyncio
    async def test_all_required_fields_present(self) -> None:
        """Every event has id, type, run_id, sequence, timestamp, source, redaction_status."""
        events = await self._collect_events([
            ("agent.message.delta", {"text": "x"}),
        ])
        for ev in events:
            assert isinstance(ev.id, str) and ev.id, "id must be non-empty str"
            assert isinstance(ev.type, str) and ev.type, "type must be non-empty str"
            assert isinstance(ev.run_id, str) and ev.run_id, "run_id must be non-empty str"
            assert isinstance(ev.sequence, int) and ev.sequence >= 1, f"seq={ev.sequence}"
            assert isinstance(ev.timestamp, str) and ev.timestamp, "timestamp str"
            assert isinstance(ev.source, str) and ev.source, "source str"
            assert isinstance(ev.redaction_status, str), "redaction_status str"

    @pytest.mark.asyncio
    async def test_source_is_valid_literal(self) -> None:
        """source must be one of the seven EventSource literals."""
        events = await self._collect_events([
            ("agent.message.delta", {"text": "x"}),
        ])
        for ev in events:
            assert ev.source in _VALID_SOURCES, f"invalid source={ev.source}"

    @pytest.mark.asyncio
    async def test_redaction_status_is_valid_literal(self) -> None:
        """redaction_status must be one of the four literals."""
        events = await self._collect_events([
            ("agent.message.delta", {"text": "x"}),
        ])
        for ev in events:
            assert ev.redaction_status in _VALID_REDACTIONS, \
                f"invalid redaction_status={ev.redaction_status}"

    @pytest.mark.asyncio
    async def test_timestamp_parses_iso8601(self) -> None:
        """timestamp is a valid ISO-8601 date-time string."""
        from datetime import datetime

        events = await self._collect_events([
            ("agent.message.delta", {"text": "x"}),
        ])
        for ev in events:
            # ISO-8601 with timezone info is parseable by datetime.fromisoformat
            parsed = datetime.fromisoformat(ev.timestamp)
            assert parsed is not None, f"unparseable timestamp={ev.timestamp}"

    @pytest.mark.asyncio
    async def test_ids_are_unique(self) -> None:
        """Every event in a run has a unique id."""
        events = await self._collect_events([
            ("agent.message.delta", {"text": "a"}),
            ("tool.started", {"toolName": "x"}),
            ("tool.completed", {"toolName": "x", "ok": True}),
        ])
        ids = {ev.id for ev in events}
        assert len(ids) == len(events), "duplicate event ids detected"

    @pytest.mark.asyncio
    async def test_optional_fields_default_to_none(self) -> None:
        """summary and payload are None when not provided."""
        events = await self._collect_events([])
        for ev in events:
            # run.started and run.completed have no user-set summary/payload
            assert ev.summary is None
            assert ev.payload is None or ev.payload == {}

    @pytest.mark.asyncio
    async def test_mock_adapter_valid_source(self) -> None:
        """Custom source on mock_adapter is propagated."""
        agent = Agent(name="x")
        adapter = mock_adapter([("run.started", None)], source="sandbox")
        run = await Runner.start(agent, task="t", adapter=adapter)
        events = [ev async for ev in run.events()]
        for ev in events:
            assert ev.source == "sandbox"


# ---------------------------------------------------------------------------
# Failure
# ---------------------------------------------------------------------------


class TestFailure:
    """Run that ends with run.failed → Result(status='failed')."""

    @pytest.mark.asyncio
    async def test_failure_result(self) -> None:
        """Script ending in run.failed yields a failed Result with error info."""
        agent = Agent(name="failer")
        adapter = mock_adapter([
            ("agent.message.delta", {"text": "oops"}),
            ("run.failed", {
                "code": "tool_error",
                "message": "Something broke",
                "retryable": True,
            }),
        ])
        run = await Runner.start(agent, task="risky", adapter=adapter)

        # Consume events to see them all
        events: list[AgentEvent] = []
        async for ev in run.events():
            events.append(ev)

        assert events[-1].type == "run.failed"

        result = await run.result()
        assert result.status == "failed"
        assert result.error is not None
        assert result.error["code"] == "tool_error"
        assert result.error["message"] == "Something broke"
        assert result.error["retryable"] is True

    @pytest.mark.asyncio
    async def test_failure_without_explicit_payload(self) -> None:
        """run.failed with None payload still produces a failed Result."""
        agent = Agent(name="failer")
        adapter = mock_adapter([
            ("run.failed", None),
        ])
        run = await Runner.start(agent, task="risky", adapter=adapter)

        # Don't iterate events — go straight to result
        result = await run.result()
        assert result.status == "failed"
        assert result.error is not None
        assert "code" in result.error
        assert "message" in result.error


# ---------------------------------------------------------------------------
# ACP normalization (no live agent)
# ---------------------------------------------------------------------------


class TestAcpNormalization:
    """Map fake SessionUpdate stubs through acp_adapter's normalizer."""

    @staticmethod
    def _collect(updates: list[_StubUpdate]) -> list[AgentEvent]:
        """Run the stubs through acp_adapter and collect events."""
        client = _StubClient(updates)

        class _RunResult:
            events: list[AgentEvent] = []

        result = _RunResult()

        async def _run() -> None:
            adapter = acp_adapter(client)
            async for ev in adapter.run("fake-task", run_id="test-run"):
                result.events.append(ev)

        import asyncio
        asyncio.run(_run())
        return result.events

    # -- Mapping assertions: every ACP update kind maps as specified -------

    def test_text_delta_maps_to_agent_message_delta(self) -> None:
        events = self._collect([
            _StubUpdate(UpdateKind.TextDelta, text="Hello"),
        ])
        assert any(
            e.type == "agent.message.delta" and e.payload == {"text": "Hello"}
            for e in events
        ), "TextDelta → agent.message.delta missing"

    def test_thought_delta_maps_to_agent_thought_summary(self) -> None:
        events = self._collect([
            _StubUpdate(UpdateKind.ThoughtDelta, text="thinking..."),
        ])
        assert any(
            e.type == "agent.thought_summary" and e.payload == {"text": "thinking..."}
            for e in events
        ), "ThoughtDelta → agent.thought_summary missing"

    def test_tool_use_start_maps_to_tool_started(self) -> None:
        events = self._collect([
            _StubUpdate(
                UpdateKind.ToolUseStart,
                tool_name="read_file",
                tool_use_id="call-1",
                tool_input='{"path": "/tmp/x"}',
            ),
        ])
        matches = [
            e for e in events
            if e.type == "tool.started"
            and e.payload
            and e.payload.get("toolName") == "read_file"
            and e.payload.get("callId") == "call-1"
            and e.payload.get("inputPreview") == {"path": "/tmp/x"}
        ]
        assert matches, "ToolUseStart → tool.started missing or wrong payload"

    def test_tool_use_end_maps_to_tool_completed(self) -> None:
        events = self._collect([
            _StubUpdate(
                UpdateKind.ToolUseEnd,
                tool_name="read_file",
                tool_use_id="call-1",
                tool_status="success",
            ),
        ])
        matches = [
            e for e in events
            if e.type == "tool.completed"
            and e.payload
            and e.payload.get("toolName") == "read_file"
            and e.payload.get("ok") is True
        ]
        assert matches, "ToolUseEnd → tool.completed missing"

    def test_done_maps_to_run_completed(self) -> None:
        events = self._collect([
            _StubUpdate(UpdateKind.Done),
        ])
        assert any(e.type == "run.completed" for e in events), \
            "Done → run.completed missing"

    def test_error_maps_to_run_failed(self) -> None:
        events = self._collect([
            _StubUpdate(UpdateKind.Error, error="crash"),
        ])
        matches = [
            e for e in events
            if e.type == "run.failed"
            and e.payload
            and e.payload.get("code") == "agent_error"
            and e.payload.get("message") == "crash"
        ]
        assert matches, "Error → run.failed missing or wrong payload"

    def test_usage_maps_to_agent_usage(self) -> None:
        events = self._collect([
            _StubUpdate(UpdateKind.Usage, usage_json='{"tokens": 42}'),
        ])
        matches = [
            e for e in events
            if e.type == "agent.usage"
            and e.payload
            and e.payload.get("tokens") == 42
        ]
        assert matches, "Usage → agent.usage missing"

    # -- run.started is always emitted before any update events -------------

    def test_run_started_is_emitted_first(self) -> None:
        events = self._collect([
            _StubUpdate(UpdateKind.TextDelta, text="hi"),
            _StubUpdate(UpdateKind.Done),
        ])
        assert events[0].type == "run.started", \
            "First event must be run.started"

    def test_acp_run_completed_not_duplicated(self) -> None:
        """When Done is received, no extra run.completed is appended."""
        events = self._collect([
            _StubUpdate(UpdateKind.Done),
        ])
        completion_events = [e for e in events if e.type == "run.completed"]
        assert len(completion_events) == 1, \
            f"Expected exactly one run.completed, got {len(completion_events)}"

    def test_acp_terminates_without_done(self) -> None:
        """Stream ending without Done still emits run.completed."""
        events = self._collect([
            _StubUpdate(UpdateKind.TextDelta, text="ok"),
        ])
        completion_events = [e for e in events if e.type == "run.completed"]
        assert len(completion_events) == 1, \
            "run.completed must be emitted even without Done signal"

    # -- Unknown update kinds are forwarded as agent.update -----------------

    def test_unknown_kind_forwarded_as_agent_update(self) -> None:
        """Unrecognised UpdateKind values map to agent.update."""
        events = self._collect([
            _StubUpdate(UpdateKind.RateLimit,
                        rate_limit_json='{"limit": 10, "remaining": 3}'),
        ])
        updates = [e for e in events if e.type == "agent.update"]
        assert len(updates) >= 1, "Unmapped kind must emit agent.update"
        # RateLimit is in _GENERIC set, so it maps to agent.update
