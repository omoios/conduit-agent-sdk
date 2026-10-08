"""Pure-sync reducer canary tests (Phase 2).

Drives :func:`runcore.step` / :func:`runcore.finalize` directly — no event loop,
no adapters — to prove the run core is pure data logic (the Stage-2 portability
proof). Seeded ``new_id`` + fixed event timestamps make the Result reproducible.
"""

from __future__ import annotations

from conduit_sdk.runcore import (
    AgentEvent,
    RunState,
    finalize,
    step,
)


def _raw(type_: str, *, ts: str = "T", source: str = "sdk",
         summary: str | None = None, payload: dict | None = None,
         run_id: str = "r") -> AgentEvent:
    return AgentEvent(
        id="x", type=type_, run_id=run_id, sequence=0, timestamp=ts,
        source=source, redaction_status="none", summary=summary, payload=payload,
    )


def _counter_id():
    i = 0
    def factory() -> str:
        nonlocal i
        i += 1
        return f"id_{i}"
    return factory


# ---------------------------------------------------------------------------
# Sequencing + evidence + finalize
# ---------------------------------------------------------------------------


class TestReducerCanary:
    def test_basic_run_sequences_and_finalizes(self) -> None:
        state = RunState(run_id="r")
        nid = _counter_id()
        out: list[AgentEvent] = []
        for r in [
            _raw("run.started", ts="T0"),
            _raw("agent.message.delta", ts="T1",
                 payload={"text": "Done", "channel": "final"}),
            _raw("run.completed", ts="T2", summary="Done"),
        ]:
            out += step(state, r, now=100.0, new_id=nid)

        assert [e.sequence for e in out] == [1, 2, 3]
        assert [e.type for e in out] == ["run.started", "agent.message.delta", "run.completed"]
        assert state.status == "completed"
        assert state.started_at == "T0"
        assert state.last_timestamp == "T2"

        result = finalize(state)
        assert result.status == "completed"
        assert result.event_count == 3
        assert result.final_output == "Done"
        assert result.summary == "Done"
        assert result.changed_files == []
        assert result.started_at == "T0"
        assert result.ended_at == "T2"
        assert result.error is None

    def test_terminal_state_drops_further_input(self) -> None:
        state = RunState(run_id="r")
        nid = _counter_id()
        step(state, _raw("run.started", ts="T0"), now=0.0, new_id=nid)
        step(state, _raw("run.completed", ts="T1"), now=0.0, new_id=nid)
        # Already terminal — a late event is dropped.
        out = step(state, _raw("agent.message.delta", ts="T2"), now=0.0, new_id=nid)
        assert out == []
        assert state.seq_counter == 2

    def test_failed_without_payload_synthesizes_default_error(self) -> None:
        state = RunState(run_id="r")
        nid = _counter_id()
        step(state, _raw("run.started", ts="T0"), now=0.0, new_id=nid)
        step(state, _raw("run.failed", ts="T1"), now=0.0, new_id=nid)
        result = finalize(state)
        assert result.status == "failed"
        assert result.error is not None
        assert result.error["code"] == "unknown"
        assert result.error["message"] == "Run failed"

    def test_failed_with_payload_carries_failure(self) -> None:
        state = RunState(run_id="r")
        nid = _counter_id()
        step(state, _raw("run.failed", ts="T0",
                         payload={"code": "tool_error", "message": "boom",
                                  "retryable": True}), now=0.0, new_id=nid)
        result = finalize(state)
        assert result.error == {"code": "tool_error", "message": "boom", "retryable": True}


# ---------------------------------------------------------------------------
# Deadline (timeout) — pure, via injected `now`
# ---------------------------------------------------------------------------


class TestReducerDeadline:
    def test_tick_before_deadline_emits_nothing(self) -> None:
        state = RunState(run_id="r", deadline=100.0)
        nid = _counter_id()
        step(state, _raw("run.started", ts="T0"), now=50.0, new_id=nid)
        out = step(state, None, now=90.0, new_id=nid)  # tick, before deadline
        assert out == []
        assert state.status == "running"

    def test_tick_past_deadline_emits_timed_out(self) -> None:
        state = RunState(run_id="r", deadline=100.0)
        nid = _counter_id()
        step(state, _raw("run.started", ts="T0"), now=50.0, new_id=nid)
        out = step(state, None, now=150.0, new_id=nid)
        types = [e.type for e in out]
        assert types == ["run.cancelling", "run.timed_out"]
        assert state.status == "timed_out"
        result = finalize(state)
        assert result.status == "timed_out"
        assert result.event_count == 3  # started + cancelling + timed_out

    def test_event_past_deadline_also_triggers_timeout(self) -> None:
        state = RunState(run_id="r", deadline=100.0)
        nid = _counter_id()
        step(state, _raw("run.started", ts="T0"), now=50.0, new_id=nid)
        # A real event arriving past the deadline triggers timeout, not passthrough.
        out = step(state, _raw("agent.message.delta", ts="T1"), now=150.0, new_id=nid)
        assert [e.type for e in out] == ["run.cancelling", "run.timed_out"]
        assert state.status == "timed_out"


# ---------------------------------------------------------------------------
# Evidence collection rules
# ---------------------------------------------------------------------------


class TestReducerEvidence:
    def test_changed_files_dedup_and_diff_last_wins(self) -> None:
        state = RunState(run_id="r")
        nid = _counter_id()
        step(state, _raw("run.started", ts="T0"), now=0.0, new_id=nid)
        step(state, _raw("diff.preview_created", ts="T1",
                         payload={"files": ["a.py", "b.py"], "diff": "@@1"}), now=0.0, new_id=nid)
        step(state, _raw("file.edited", ts="T2", payload={"path": "a.py"}), now=0.0, new_id=nid)
        step(state, _raw("diff.applied", ts="T3",
                         payload={"files": ["c.py"], "diff": "@@2"}), now=0.0, new_id=nid)
        result = finalize(state)
        assert result.changed_files == ["a.py", "b.py", "c.py"]
        assert result.diff == "@@2"

    def test_test_completed_recorded(self) -> None:
        state = RunState(run_id="r")
        nid = _counter_id()
        step(state, _raw("test.completed", ts="T0",
                         payload={"command": "pytest", "exitCode": 0, "passed": True,
                                  "durationMs": 12}), now=0.0, new_id=nid)
        result = finalize(state)
        assert len(result.tests) == 1
        assert result.tests[0]["command"] == "pytest"
        assert result.tests[0]["exitCode"] == 0
        assert result.tests[0]["passed"] is True

    def test_approval_events_recorded(self) -> None:
        state = RunState(run_id="r")
        nid = _counter_id()
        step(state, _raw("approval.approved", ts="T0",
                         payload={"approvalId": "a1", "reason": "ok"}), now=0.0, new_id=nid)
        step(state, _raw("approval.rejected", ts="T1",
                         payload={"approvalId": "a2", "reason": "no"}), now=0.0, new_id=nid)
        result = finalize(state)
        decisions = [(a["id"], a["decision"]) for a in result.approvals]
        assert decisions == [("a1", "approved"), ("a2", "rejected")]

    def test_usage_merge_camelcase(self) -> None:
        state = RunState(run_id="r")
        nid = _counter_id()
        step(state, _raw("cost.updated", ts="T0",
                         payload={"inputTokens": 10, "costUsd": 0.01}), now=0.0, new_id=nid)
        step(state, _raw("usage", ts="T1",
                         payload={"output_tokens": 5, "duration_ms": 200}), now=0.0, new_id=nid)
        result = finalize(state)
        assert result.usage == {"inputTokens": 10, "costUsd": 0.01,
                                "outputTokens": 5, "durationMs": 200}

    def test_final_output_fallback_to_terminal_summary(self) -> None:
        state = RunState(run_id="r")
        nid = _counter_id()
        step(state, _raw("run.started", ts="T0"), now=0.0, new_id=nid)
        step(state, _raw("run.completed", ts="T1", summary="all good"), now=0.0, new_id=nid)
        result = finalize(state)
        # No agent.message.* → final_output falls back to terminal summary.
        assert result.final_output == "all good"
