"""Run layer — normalized event subsystem for conduit-agent-sdk.

Implements the Background Agent SDK's event-driven run abstraction:
- ``AgentEvent`` envelope (per ``EVENTS.md``)
- ``Result`` accumulator
- ``Adapter`` protocol with ``mock`` and ``acp`` backends
- ``Run`` + ``Runner`` lifecycle

Types and field values follow the seed specification exactly, with
Python snake_case naming.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

__all__ = [
    "Agent",
    "AgentEvent",
    "Result",
    "Adapter",
    "mock_adapter",
    "acp_adapter",
    "Run",
    "Runner",
]

_VALID_SOURCES = frozenset({
    "sdk", "adapter", "proxy", "agent", "sandbox", "controller", "server",
})
_VALID_REDACTIONS = frozenset({"none", "redacted", "blocked", "unknown"})
_VALID_STATUSES = frozenset({"completed", "failed", "cancelled"})

_TERMINAL_TYPES = frozenset({"run.completed", "run.failed", "run.cancelled"})


def _utcnow() -> str:
    """Return current UTC time as an ISO-8601 string (no microseconds)."""
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    """Return a short unique hex identifier."""
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# Public data types
# ---------------------------------------------------------------------------


@dataclass
class Agent:
    """Opaque agent configuration passed to :meth:`Runner.start`."""

    name: str
    instructions: str = ""


@dataclass
class AgentEvent:
    """Normalized event envelope (per ``EVENTS.md``).

    All fields are required at construction except *summary* and *payload*,
    which default to ``None``.
    """

    id: str
    type: str
    run_id: str
    sequence: int
    timestamp: str
    source: str
    redaction_status: str
    summary: str | None = None
    payload: dict | None = None


@dataclass
class Result:
    """Final result of a completed run.

    Computed from the terminal ``run.*`` event after all events have been
    consumed.  *status* is one of ``"completed"``, ``"failed"``, or
    ``"cancelled"``.
    """

    run_id: str
    status: str
    event_count: int
    summary: str | None = None
    error: dict | None = None
    started_at: str = ""
    ended_at: str = ""


# ---------------------------------------------------------------------------
# Adapter protocol
# ---------------------------------------------------------------------------


class Adapter(Protocol):
    """Pluggable execution backend that yields normalized ``AgentEvent`` s.

    Adapters should yield events with *sequence* set to ``0``; the
    :class:`Run` wrapper assigns monotonically increasing sequence numbers.
    """

    @property
    def name(self) -> str:
        """Short human-readable name of this adapter."""

    async def run(
        self, task: str, *, run_id: str
    ) -> AsyncIterator[AgentEvent]:
        """Yield normalized events for executing *task*.

        Parameters
        ----------
        task:
            The prompt or instruction to execute.
        run_id:
            Stable identifier for the enclosing run.
        """
        ...


# ---------------------------------------------------------------------------
# Built-in adapters
# ---------------------------------------------------------------------------


def mock_adapter(
    script: list[tuple[str, dict | None]],
    *,
    source: str = "adapter",
    redaction_status: str = "none",
) -> Adapter:
    """Deterministic mock adapter built from a list of ``(type, payload)`` tuples.
    Automatically inserts a leading ``run.started`` and a trailing
    ``run.completed`` / ``run.failed`` if not already present in *script*.

    Sequence numbers are assigned by the :class:`Run` wrapper, so this
    adapter emits ``0`` for every event.
    """
    events = _build_mock_script(script, source=source, redaction_status=redaction_status)

    class _MockAdapter:
        name = "mock"

        async def run(
            self, task: str, *, run_id: str
        ) -> AsyncIterator[AgentEvent]:
            for ev in events:
                yield AgentEvent(
                    id=_new_id(),
                    type=ev["type"],
                    run_id=run_id,
                    sequence=0,
                    timestamp=_utcnow(),
                    source=source,
                    redaction_status=redaction_status,
                    summary=ev.get("summary"),
                    payload=ev.get("payload"),
                )

    return _MockAdapter()


def _build_mock_script(
    script: list,
    *,
    source: str,
    redaction_status: str,
) -> list[dict[str, Any]]:
    """Expand a mock script, filling missing lifecycle events."""
    result: list[dict[str, Any]] = []
    has_started = False
    has_terminal = False

    for item in script:
        if isinstance(item, tuple):
            type_, payload = item
        elif isinstance(item, str):
            type_, payload = item, None
        elif isinstance(item, dict):
            type_ = item.get("type", "agent.update")
            payload = item.get("payload")
        else:
            raise TypeError(
                f"Expected tuple, str, or dict, got {type(item).__name__}"
            )

        has_started = has_started or type_ == "run.started"
        has_terminal = has_terminal or type_ in _TERMINAL_TYPES

        result.append({
            "type": type_,
            "payload": payload,
            "source": source,
            "redaction_status": redaction_status,
        })

    if not has_started:
        result.insert(0, {
            "type": "run.started",
            "payload": None,
            "source": source,
            "redaction_status": redaction_status,
        })

    if not has_terminal:
        result.append({
            "type": "run.completed",
            "payload": None,
            "source": source,
            "redaction_status": redaction_status,
        })

    return result


def acp_adapter(client: Any) -> Adapter:
    """Wrap a :class:`conduit_sdk.Client` as an :class:`Adapter`.

    Calls ``client.prompt_stream(task)`` and normalises each
    ``SessionUpdate`` into the event catalog (``run.started``,
    ``agent.message.delta``, ``tool.started``, ``tool.completed``,
    ``run.failed``, ``run.completed``, etc.).

    Parameters
    ----------
    client:
        An instance of :class:`conduit_sdk.Client` (or any object with a
        ``prompt_stream`` method that returns ``SessionUpdate``-like values).
    """
    import json
    from conduit_sdk._conduit_sdk import UpdateKind

    # Cache enum integer values for fast dispatch.
    # (Rust IntEnum is unhashable, so we store .value in sets.)
    _DELTA_V = int(UpdateKind.TextDelta)
    _THOUGHT_V = int(UpdateKind.ThoughtDelta)
    _TOOL_START_V = int(UpdateKind.ToolUseStart)
    _TOOL_END_V = int(UpdateKind.ToolUseEnd)
    _USAGE_V = int(UpdateKind.Usage)
    _DONE_V = int(UpdateKind.Done)
    _ERROR_V = int(UpdateKind.Error)

    # Group of "generic" kinds that map to ``agent.update``.
    _GENERIC_VALUES = frozenset({
        int(UpdateKind.ToolUseUpdate),
        int(UpdateKind.ModeChange),
        int(UpdateKind.Plan),
        int(UpdateKind.ConfigUpdate),
        int(UpdateKind.CommandsUpdate),
        int(UpdateKind.SessionInfo),
        int(UpdateKind.RateLimit),
    })


    class _AcpAdapter:
        name = "acp"

        async def run(
            self, task: str, *, run_id: str
        ) -> AsyncIterator[AgentEvent]:
            def _ev(
                type_: str,
                *,
                source: str = "adapter",
                payload: dict[str, Any] | None = None,
                summary: str | None = None,
            ) -> AgentEvent:
                return AgentEvent(
                    id=_new_id(),
                    type=type_,
                    run_id=run_id,
                    sequence=0,
                    timestamp=_utcnow(),
                    source=source,
                    redaction_status="none",
                    summary=summary,
                    payload=payload,
                )

            # Leading lifecycle event.
            yield _ev("run.started", source="sdk")

            saw_terminal = False
            async for update in client.prompt_stream(task):
                kv = int(update.kind)  # int — Rust IntEnum has no .value

                if kv == _DELTA_V:
                    yield _ev("agent.message.delta",
                              payload={"text": update.text or ""})

                elif kv == _THOUGHT_V:
                    yield _ev("agent.thought_summary",
                              payload={"text": update.text or ""})

                elif kv == _TOOL_START_V:
                    payload: dict[str, Any] = {
                        "toolName": update.tool_name or "",
                    }
                    if update.tool_use_id:
                        payload["callId"] = update.tool_use_id
                    if update.tool_input:
                        try:
                            payload["inputPreview"] = json.loads(
                                update.tool_input
                            )
                        except (TypeError, ValueError):
                            payload["inputPreview"] = update.tool_input
                    yield _ev("tool.started", payload=payload)

                elif kv == _TOOL_END_V:
                    ok = True
                    if update.tool_status == "error":
                        ok = False
                    elif update.tool_status == "success":
                        ok = True
                    payload = {
                        "toolName": update.tool_name or "",
                        "ok": ok,
                    }
                    if update.tool_use_id:
                        payload["callId"] = update.tool_use_id
                    if update.tool_content:
                        try:
                            payload["outputPreview"] = json.loads(
                                update.tool_content
                            )
                        except (TypeError, ValueError):
                            payload["outputPreview"] = update.tool_content
                    yield _ev("tool.completed", payload=payload)

                elif kv == _USAGE_V:
                    pl: dict[str, Any] = {}
                    if update.usage_json:
                        try:
                            pl = json.loads(update.usage_json)
                        except (TypeError, ValueError):
                            pl = {"raw": update.usage_json}
                    yield _ev("agent.usage", payload=pl)

                elif kv == _ERROR_V:
                    saw_terminal = True
                    yield _ev("run.failed", payload={
                        "code": "agent_error",
                        "message": update.error or "Unknown agent error",
                        "retryable": False,
                    })

                elif kv == _DONE_V:
                    saw_terminal = True
                    yield _ev("run.completed")

                elif kv in _GENERIC_VALUES:
                    raw: dict[str, Any] = {
                        "kind": str(update.kind).rsplit(".", maxsplit=1)[-1],
                    }
                    if update.text:
                        raw["text"] = update.text
                    if update.tool_name:
                        raw["toolName"] = update.tool_name
                    if update.error:
                        raw["error"] = update.error
                    yield _ev("agent.update", payload=raw)

                else:
                    raw = {"kind": str(update.kind)}
                    if update.text:
                        raw["text"] = update.text
                    if update.tool_name:
                        raw["toolName"] = update.tool_name
                    if update.error:
                        raw["error"] = update.error
                    yield _ev("agent.update", payload=raw)

            # Stream ended without a terminal event → emit completed.
            if not saw_terminal:
                yield _ev("run.completed")

    return _AcpAdapter()


# ---------------------------------------------------------------------------
# Run + Runner
# ---------------------------------------------------------------------------


class Run:
    """A bounded execution that produces a normalized event stream.

    Wraps an :class:`Adapter` and assigns monotonically increasing sequence
    numbers.  An internal buffer ensures :meth:`result` can be called even
    when :meth:`events` has not been fully consumed.
    """

    def __init__(
        self,
        run_id: str,
        adapter: Adapter,
        task: str,
        agent: Agent,
    ) -> None:
        self._run_id = run_id
        self._adapter = adapter
        self._task = task
        self._agent = agent
        self._buffer: list[AgentEvent] = []
        self._adapter_iter: AsyncIterator[AgentEvent] | None = None
        self._seq_counter = 0
        self._result_cache: Result | None = None

    # -- Public properties --------------------------------------------------

    @property
    def run_id(self) -> str:
        """Unique identifier for this run."""
        return self._run_id

    @property
    def agent(self) -> Agent:
        """The agent configuration this run was started with."""
        return self._agent

    # -- Core API -----------------------------------------------------------

    async def _next_event(self) -> AgentEvent | None:
        """Pull one event from the adapter, assign a sequence, and buffer it.

        Returns ``None`` when the adapter stream is exhausted.
        """
        if self._adapter_iter is None:
            self._adapter_iter = self._adapter.run(
                self._task, run_id=self._run_id,
            )
        try:
            raw = await self._adapter_iter.__anext__()
        except StopAsyncIteration:
            return None

        self._seq_counter += 1
        ev = AgentEvent(
            id=raw.id,
            type=raw.type,
            run_id=raw.run_id,
            sequence=self._seq_counter,
            timestamp=raw.timestamp,
            source=raw.source,
            redaction_status=raw.redaction_status,
            summary=raw.summary,
            payload=raw.payload,
        )
        self._buffer.append(ev)
        return ev

    async def events(self) -> AsyncIterator[AgentEvent]:
        """Yield every event with monotonically increasing sequence numbers.

        The first call to ``events()`` (or :meth:`result`) starts the
        underlying adapter.  Subsequent calls continue from where the last
        iterator left off.
        """
        # Yield already-buffered events first (they carry valid sequences).
        idx = 0
        while idx < len(self._buffer):
            yield self._buffer[idx]
            idx += 1

        # Stream remaining events from the adapter.
        while True:
            ev = await self._next_event()
            if ev is None:
                break
            yield ev

    async def result(self) -> Result:
        """Consume all remaining events and return the computed ``Result``.

        Safe to call even when :meth:`events` has not been called, has
        been partially iterated, or has already been fully consumed.
        """
        if self._result_cache is not None:
            return self._result_cache

        # Drain the adapter entirely.
        while True:
            ev = await self._next_event()
            if ev is None:
                break

        if not self._buffer:
            self._result_cache = Result(
                run_id=self._run_id,
                status="completed",
                event_count=0,
                started_at=_utcnow(),
                ended_at=_utcnow(),
            )
            return self._result_cache

        last = self._buffer[-1]

        # Locate the ``run.started`` timestamp.
        started_at = ""
        for ev in self._buffer:
            if ev.type == "run.started":
                started_at = ev.timestamp
                break

        event_count = len(self._buffer)
        ended_at = _utcnow()

        if last.type == "run.failed":
            status = "failed"
            error = last.payload or {
                "code": "unknown", "message": "Run failed",
            }
            summary = last.summary
        elif last.type == "run.cancelled":
            status = "cancelled"
            error = None
            summary = last.summary or "Run cancelled"
        else:
            status = "completed"
            error = None
            summary = last.summary

        self._result_cache = Result(
            run_id=self._run_id,
            status=status,
            event_count=event_count,
            summary=summary,
            error=error,
            started_at=started_at,
            ended_at=ended_at,
        )
        return self._result_cache


class Runner:
    """Factory for creating and starting :class:`Run` instances."""

    @classmethod
    async def start(
        cls,
        agent: Agent,
        *,
        task: str,
        adapter: Adapter,
    ) -> Run:
        """Create a new :class:`Run` wired to the given *adapter*.

        Parameters
        ----------
        agent:
            Opaque agent configuration (name, instructions).
        task:
            The prompt or instruction to execute.
        adapter:
            The execution backend (mock, acp, etc.).
        """
        run_id = _new_id()
        return Run(run_id=run_id, adapter=adapter, task=task, agent=agent)
