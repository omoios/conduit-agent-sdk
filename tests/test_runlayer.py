"""Tests for conduit_sdk.runlayer — Run layer.

Covers mock-run lifecycle, event schema validation, failure semantics,
and ACP normalization without a live agent.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

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

def _content(text: str) -> str:
    """Build an ACP tool_content JSON string carrying one text block."""
    return json.dumps([{"type": "content", "content": {"type": "text", "text": text}}])


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
    """A mock client that yields stub updates as canonical SessionEvents.

    The real ``Client.prompt_stream`` yields :class:`SessionEvent` objects, so
    each stub is run through :func:`normalize` to match that contract.
    """

    def __init__(self, updates: list[_StubUpdate]) -> None:
        self._updates = updates

    async def prompt_stream(
        self, text: str, *, session_id: str | None = None
    ) -> AsyncIterator[Any]:
        from conduit_sdk.events import normalize

        for u in self._updates:
            yield normalize(u)


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

    @pytest.mark.asyncio
    async def test_mock_summary_carries_through(self) -> None:
        """A dict item carrying `summary` propagates it to the emitted event (P1 fix)."""
        agent = Agent(name="x")
        adapter = mock_adapter([
            {"type": "agent.message.delta", "payload": {"text": "Done"}},
            {"type": "run.completed", "summary": "Done"},
        ])
        run = await Runner.start(agent, task="t", adapter=adapter)
        events = [e async for e in run.events()]
        completed = [e for e in events if e.type == "run.completed"]
        assert completed and completed[0].summary == "Done"

    @pytest.mark.asyncio
    async def test_mock_three_tuple_carries_summary(self) -> None:
        """A 3-tuple (type, payload, summary) is supported (P1)."""
        agent = Agent(name="x")
        adapter = mock_adapter([
            ("run.completed", None, "all done"),
        ])
        run = await Runner.start(agent, task="t", adapter=adapter)
        events = [e async for e in run.events()]
        assert events[-1].type == "run.completed"
        assert events[-1].summary == "all done"

    @pytest.mark.asyncio
    async def test_result_has_empty_evidence_defaults(self) -> None:
        """Result constructs with the evidence fields defaulted (P1)."""
        r = Result(run_id="r", status="completed", event_count=0)
        assert r.changed_files == []
        assert r.tests == []
        assert r.approvals == []
        assert r.artifacts == []
        assert r.final_output is None
        assert r.diff is None
        assert r.pr is None
        assert r.usage is None
        # error remains the seed failure shape slot (None until a run.failed).
        assert r.error is None


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
# Queue-driver: status, cancel, timeout
# ---------------------------------------------------------------------------


class TestQueueDriver:
    """Run as a single-consumer queue driver (Phase 2)."""

    @pytest.mark.asyncio
    async def test_status_running_before_terminal(self) -> None:
        adapter = mock_adapter([("agent.message.delta", {"text": "hi"})])
        run = await Runner.start(Agent(name="x"), task="t", adapter=adapter)
        # status() is available before draining; the run has not yet started.
        assert run.status() == "running"
        await run.result()
        assert run.status() == "completed"

    @pytest.mark.asyncio
    async def test_cancel_yields_cancelled(self) -> None:
        import asyncio

        started = asyncio.Event()

        class _SlowAdapter:
            name = "slow"

            async def run(self, task, *, run_id):
                yield AgentEvent(id="i", type="run.started", run_id=run_id,
                                 sequence=0, timestamp="t", source="sdk",
                                 redaction_status="none")
                started.set()
                try:
                    await asyncio.sleep(30)
                except asyncio.CancelledError:
                    return
                yield AgentEvent(id="i2", type="run.completed", run_id=run_id,
                                 sequence=0, timestamp="t", source="sdk",
                                 redaction_status="none")

        run = await Runner.start(Agent(name="x"), task="t", adapter=_SlowAdapter())
        events: list[AgentEvent] = []

        async def consume() -> None:
            async for ev in run.events():
                events.append(ev)

        consumer = asyncio.create_task(consume())
        await started.wait()
        await run.cancel("user requested")
        await consumer
        from tests._adapter_contract import _assert_contract
        _assert_contract(events)
        assert run.status() == "cancelled"
        result = await run.result()
        assert result.status == "cancelled"

    @pytest.mark.asyncio
    async def test_timeout_produces_timed_out(self) -> None:
        import asyncio

        class _BlockingAdapter:
            name = "block"

            async def run(self, task, *, run_id):
                yield AgentEvent(id="i", type="run.started", run_id=run_id,
                                 sequence=0, timestamp="t", source="sdk",
                                 redaction_status="none")
                await asyncio.sleep(10)  # never completes within the budget
                yield AgentEvent(id="i2", type="run.completed", run_id=run_id,
                                 sequence=0, timestamp="t", source="sdk",
                                 redaction_status="none")

        run = await Runner.start(
            Agent(name="x"), task="t", adapter=_BlockingAdapter(), timeout=0.2,
        )
        result = await run.result()
        assert result.status == "timed_out"
        assert result.event_count == 3  # started + cancelling + timed_out


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
            e.type == "agent.message.delta"
            and e.payload.get("text") == "Hello"
            and e.payload.get("channel") == "final"
            for e in events
        ), "TextDelta → agent.message.delta (channel=final) missing"

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

    def test_terminal_tool_update_maps_to_tool_completed(self) -> None:
        # A terminal ToolCallUpdate (status completed/failed) — preceded by the
        # ToolCallStart that registers the title — becomes tool.completed.
        events = self._collect([
            _StubUpdate(
                UpdateKind.ToolUseStart,
                tool_name="read_file",
                tool_use_id="call-1",
            ),
            _StubUpdate(
                UpdateKind.ToolUseUpdate,
                tool_use_id="call-1",
                tool_status="completed",
                tool_content=_content("done"),
            ),
        ])
        matches = [
            e for e in events
            if e.type == "tool.completed"
            and e.payload
            and e.payload.get("toolName") == "read_file"
            and e.payload.get("ok") is True
            and e.payload.get("callId") == "call-1"
            and e.payload.get("outputPreview") == "done"
        ]
        assert matches, "terminal ToolCallUpdate → tool.completed missing or wrong"

    def test_done_maps_to_run_completed(self) -> None:
        events = self._collect([
            _StubUpdate(UpdateKind.Done),
        ])
        assert any(e.type == "run.completed" for e in events), \
            "Done → run.completed missing"

    def test_error_maps_to_agent_update(self) -> None:
        # UpdateKind.Error normalizes to Unknown (failures surface as exceptions,
        # not events — see test_conduit_error_maps_to_run_failed).
        events = self._collect([
            _StubUpdate(UpdateKind.Error, error="crash"),
        ])
        matches = [
            e for e in events
            if e.type == "agent.update"
            and e.payload
            and e.payload.get("event") == "unknown"
            and e.payload.get("kind") == "error"
        ]
        assert matches, "Error → agent.update (Unknown) missing or wrong"

    def test_conduit_error_maps_to_run_failed(self) -> None:
        # A client whose prompt_stream raises ConduitError → run.failed.
        from conduit_sdk.exceptions import ConduitError

        class _RaisingClient:
            async def prompt_stream(self, text, *, session_id=None):
                raise ConduitError("agent exploded")
                yield  # unreachable — makes this an async generator

        async def _run() -> list[AgentEvent]:
            adapter = acp_adapter(_RaisingClient())
            return [ev async for ev in adapter.run("fake-task", run_id="test-run")]

        import asyncio
        events = asyncio.run(_run())
        matches = [
            e for e in events
            if e.type == "run.failed"
            and e.payload
            and e.payload.get("code") == "agent_error"
            and "exploded" in e.payload.get("message", "")
        ]
        assert matches, "ConduitError → run.failed missing or wrong"

    def test_usage_maps_to_budget_updated(self) -> None:
        # Per the catalog, Usage has a dedicated event now → budget.updated.
        events = self._collect([
            _StubUpdate(UpdateKind.Usage, usage_json='{"used": 42, "size": 100}'),
        ])
        matches = [
            e for e in events
            if e.type == "budget.updated"
            and e.payload
            and e.payload.get("used") == 42
            and e.payload.get("size") == 100
        ]
        assert matches, "Usage → budget.updated missing or wrong"

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

    def test_rate_limit_maps_to_rate_limited(self) -> None:
        """RateLimit now maps to its own named event (no longer agent.update)."""
        events = self._collect([
            _StubUpdate(UpdateKind.RateLimit,
                        rate_limit_json='{"limit": 10, "remaining": 3}'),
        ])
        limited = [e for e in events if e.type == "rate.limited"]
        assert len(limited) >= 1, "RateLimit → rate.limited missing"


class TestThoughtModes:
    """include_thoughts controls the reasoning stream shape (delta / summary / off)."""

    @staticmethod
    def _collect(updates, *, include_thoughts):
        client = _StubClient(updates)
        events: list[AgentEvent] = []

        async def _run() -> None:
            adapter = acp_adapter(client, include_thoughts=include_thoughts)
            async for ev in adapter.run("t", run_id="r"):
                events.append(ev)

        import asyncio
        asyncio.run(_run())
        return events

    def test_summary_coalesces_chunks_into_one_summary(self) -> None:
        # Multiple ThoughtDeltas, then a TextDelta boundary, then Done.
        events = self._collect([
            _StubUpdate(UpdateKind.ThoughtDelta, text="Plan A. "),
            _StubUpdate(UpdateKind.ThoughtDelta, text="Then B."),
            _StubUpdate(UpdateKind.TextDelta, text="ok"),
            _StubUpdate(UpdateKind.Done),
        ], include_thoughts="summary")
        summaries = [e for e in events if e.type == "agent.thought_summary"]
        deltas = [e for e in events if e.type == "agent.thought.delta"]
        assert len(summaries) == 1, f"expected 1 coalesced summary, got {len(summaries)}"
        assert summaries[0].payload["text"] == "Plan A. Then B."
        assert deltas == [], "summary mode must not emit thought.delta"

    def test_delta_emits_one_per_chunk(self) -> None:
        events = self._collect([
            _StubUpdate(UpdateKind.ThoughtDelta, text="a"),
            _StubUpdate(UpdateKind.ThoughtDelta, text="b"),
            _StubUpdate(UpdateKind.Done),
        ], include_thoughts="delta")
        deltas = [e for e in events if e.type == "agent.thought.delta"]
        assert [e.payload["text"] for e in deltas] == ["a", "b"]
        assert not any(e.type == "agent.thought_summary" for e in events)

    def test_false_drops_all_thought_events(self) -> None:
        events = self._collect([
            _StubUpdate(UpdateKind.ThoughtDelta, text="hidden"),
            _StubUpdate(UpdateKind.Done),
        ], include_thoughts=False)
        assert not any(e.type.startswith("agent.thought") for e in events)
        # Lifecycle still intact.
        assert events[0].type == "run.started"
        assert events[-1].type == "run.completed"


class TestSemanticToolMapping:
    """Rich tool mapping: ToolCallStart.kind → semantic events (Slice 1)."""

    @staticmethod
    def _collect(updates):
        client = _StubClient(updates)
        events: list[AgentEvent] = []

        async def _run() -> None:
            async for ev in acp_adapter(client).run("t", run_id="r"):
                events.append(ev)

        import asyncio
        asyncio.run(_run())
        return events

    def test_edit_tool_emits_file_events(self) -> None:
        events = self._collect([
            _StubUpdate(UpdateKind.ToolUseStart, tool_use_id="t1",
                        tool_name="edit_file", tool_kind="edit",
                        tool_input='{"path": "src/app.py"}'),
            _StubUpdate(UpdateKind.ToolUseUpdate, tool_use_id="t1",
                        tool_status="completed",
                        tool_locations='["src/app.py"]'),
            _StubUpdate(UpdateKind.Done),
        ])
        types = [e.type for e in events]
        assert "file.write_requested" in types, "edit start → file.write_requested"
        assert "file.edited" in types, "edit complete → file.edited (from locations)"
        edited = [e for e in events if e.type == "file.edited"]
        assert edited[0].payload["path"] == "src/app.py"
        # Universal lifecycle completion still emitted.
        assert "tool.completed" in types

    def test_execute_tool_emits_command_started(self) -> None:
        events = self._collect([
            _StubUpdate(UpdateKind.ToolUseStart, tool_use_id="t2",
                        tool_name="shell", tool_kind="execute",
                        tool_input='{"command": "pytest"}'),
            _StubUpdate(UpdateKind.Done),
        ])
        types = [e.type for e in events]
        assert "command.started" in types
        assert "tool.started" not in types, "execute must not also emit generic tool.started"

    def test_read_tool_emits_file_read(self) -> None:
        events = self._collect([
            _StubUpdate(UpdateKind.ToolUseStart, tool_use_id="t3",
                        tool_name="read_file", tool_kind="read",
                        tool_input='{"path": "README.md"}'),
            _StubUpdate(UpdateKind.Done),
        ])
        assert any(e.type == "file.read" for e in events)
        assert not any(e.type == "tool.started" for e in events)

    def test_plan_created_then_updated(self) -> None:
        events = self._collect([
            _StubUpdate(UpdateKind.Plan, plan_json='[{"label":"a"}]'),
            _StubUpdate(UpdateKind.Plan, plan_json='[{"label":"a"},{"label":"b"}]'),
            _StubUpdate(UpdateKind.Done),
        ])
        plans = [e.type for e in events if e.type.startswith("agent.plan.")]
        assert plans == ["agent.plan.created", "agent.plan.updated"], plans

    def test_done_refusal_maps_to_run_failed(self) -> None:
        events = self._collect([
            _StubUpdate(UpdateKind.Done, stop_reason="refusal"),
        ])
        failed = [e for e in events if e.type == "run.failed"]
        assert failed and failed[0].payload["code"] == "refusal"

    def test_done_cancelled_maps_to_run_cancelled(self) -> None:
        events = self._collect([
            _StubUpdate(UpdateKind.Done, stop_reason="cancelled"),
        ])
        assert any(e.type == "run.cancelled" for e in events)
