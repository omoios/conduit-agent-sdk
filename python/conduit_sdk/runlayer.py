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

import asyncio
import inspect
import json
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, replace
from typing import Any, Protocol

from conduit_sdk.runcore import (
    _TERMINAL_TYPES,
    _VALID_REDACTIONS,  # noqa: F401  (re-exported for tests)
    _VALID_SOURCES,  # noqa: F401  (re-exported for tests)
    _VALID_STATUSES,  # noqa: F401  (re-exported for tests)
    AgentEvent,
    Result,
    RunState,
    _new_id,
    _utcnow,
    finalize,
    redact_event,
    step,
)
from conduit_sdk.policy import Action, ApprovalDecision, ApprovalRequest
from conduit_sdk.permissions import PermissionResultAllow, PermissionResultDeny
from conduit_sdk.elicitation import ElicitationResponse  # noqa: E402
__all__ = [
    "Agent",
    "AgentEvent",
    "Result",
    "Adapter",
    "mock_adapter",
    "acp_adapter",
    "process_adapter",
    "conductor_adapter",
    "query",
    "acp_agent",
    "Run",
]

# ---------------------------------------------------------------------------
# Public data types
# ---------------------------------------------------------------------------


@dataclass
class Agent:
    """Opaque agent configuration passed to :meth:`Runner.start`."""

    name: str
    instructions: str = ""
    policy: Any = None  # optional Policy (P3); used as a fallback in P4


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
    script: list,
    *,
    source: str = "adapter",
    redaction_status: str = "none",
) -> Adapter:
    """Deterministic mock adapter built from a list of script items.

    Each item may be a 2-tuple ``(type, payload)``, a 3-tuple
    ``(type, payload, summary)``, a bare ``str`` (type only), or a ``dict``
    with optional ``type``/``payload``/``summary`` keys. Automatically inserts
    a leading ``run.started`` and a trailing ``run.completed`` / ``run.failed``
    if not already present. Sequence numbers are assigned by :class:`Run`.
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
    """Expand a mock script, filling missing lifecycle events.

    Each item may be a 2-tuple ``(type, payload)``, a 3-tuple
    ``(type, payload, summary)``, a bare ``str`` (type only; payload/summary
    None), or a ``dict`` with optional ``type``/``payload``/``summary`` keys.
    The per-item ``summary`` is carried through (previously dropped for the
    dict/tuple forms — P1 fix). Synthesized lifecycle events default
    ``summary`` to ``None``.
    """
    result: list[dict[str, Any]] = []
    has_started = False
    has_terminal = False

    for item in script:
        if isinstance(item, tuple):
            type_ = item[0]
            payload = item[1] if len(item) > 1 else None
            summary = item[2] if len(item) > 2 else None
        elif isinstance(item, str):
            type_, payload, summary = item, None, None
        elif isinstance(item, dict):
            type_ = item.get("type", "agent.update")
            payload = item.get("payload")
            summary = item.get("summary")
        else:
            raise TypeError(
                f"Expected tuple, str, or dict, got {type(item).__name__}"
            )

        has_started = has_started or type_ == "run.started"
        has_terminal = has_terminal or type_ in _TERMINAL_TYPES

        result.append({
            "type": type_,
            "payload": payload,
            "summary": summary,
            "source": source,
            "redaction_status": redaction_status,
        })

    if not has_started:
        result.insert(0, {
            "type": "run.started",
            "payload": None,
            "summary": None,
            "source": source,
            "redaction_status": redaction_status,
        })

    if not has_terminal:
        result.append({
            "type": "run.completed",
            "payload": None,
            "summary": None,
            "source": source,
            "redaction_status": redaction_status,
        })

    return result


def acp_adapter(
    client: Any,
    *,
    coalesce_messages: bool = False,
    include_thoughts: bool | str = "summary",
) -> Adapter:
    """Wrap a :class:`conduit_sdk.Client` as an :class:`Adapter`.

    Consumes the canonical :class:`~conduit_sdk.events.SessionEvent` stream
    from ``client.prompt_stream(task)`` and maps each event to the run-layer
    catalog (``run.started``, ``agent.message.delta``, ``tool.started``,
    ``tool.completed``, ``run.failed``, ``run.completed``, …). A terminal
    ``ToolCallUpdate`` (status ``completed``/``failed``) becomes
    ``tool.completed`` with ``ok``/``output``/``toolName`` (the title is
    remembered from the preceding ``ToolCallStart``); a ``ConduitError`` raised
    by the client becomes ``run.failed``.

    When *coalesce_messages* is ``True`` (recommended for programmatic /
    background use), streamed ``TextDelta`` tokens are buffered and emitted as a
    single ``agent.message.completed`` at each turn boundary (any non-text
    event, or stream end) instead of N ``agent.message.delta`` crumbs.

    *include_thoughts* controls the agent's reasoning stream (ThoughtDelta),
    which is observability only — never collected into ``Result`` evidence:

    - ``"summary"`` (default): buffer chunks, emit ONE ``agent.thought_summary``
      per turn boundary (the seed wants safe summaries, not streamed CoT).
    - ``"delta"``: emit ``agent.thought.delta`` per chunk (raw stream, for UIs).
    - ``False``: drop thought events entirely (quietest programmatic stream).
    """
    from conduit_sdk.events import (
        AvailableCommands,
        ConfigUpdate,
        Done,
        ModeChange,
        Plan,
        RateLimit,
        SessionInfo,
        TextDelta,
        ThoughtDelta,
        ToolCallStart,
        ToolCallUpdate,
        ToolStatus,
        Unknown,
        Usage,
        to_record,
    )
    from conduit_sdk.exceptions import ConduitError

    # Each generic variant now maps to its own named catalog event (below).
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
            tool_titles: dict[str, str] = {}  # id -> title (filled at ToolCallStart)
            tool_kinds: dict[str, str] = {}   # id -> kind (for semantic completion)
            seen_plan = False
            text_buf: list[str] = []  # coalesced final-message tokens
            thought_buf: list[str] = []  # coalesced reasoning chunks

            def _paths_from(locations: Any) -> list[str]:
                """Best-effort file paths from a ToolCallUpdate.locations value."""
                if not locations:
                    return []
                if isinstance(locations, str):
                    try:
                        locations = json.loads(locations)
                    except (ValueError, TypeError):
                        return [locations]
                out: list[str] = []
                seq = locations if isinstance(locations, (list, tuple)) else [locations]
                for loc in seq:
                    if isinstance(loc, str):
                        out.append(loc)
                    elif isinstance(loc, dict):
                        p = loc.get("path") or loc.get("file") or loc.get("uri")
                        if p:
                            out.append(str(p))
                return out

            def _input_path(inp: Any) -> str | None:
                """Best-effort single file path from a ToolCallStart.input value."""
                if not isinstance(inp, dict):
                    return None
                for k in ("path", "file_path", "filePath", "file", "uri", "name"):
                    v = inp.get(k)
                    if isinstance(v, str) and v:
                        return v
                return None

            def _flush_text() -> list[AgentEvent]:
                """Emit buffered tokens as one agent.message.completed (coalesce mode)."""
                if coalesce_messages and text_buf:
                    full = "".join(text_buf)
                    text_buf.clear()
                    return [_ev("agent.message.completed", payload={"text": full})]
                return []

            def _flush_thought() -> list[AgentEvent]:
                """Emit buffered reasoning as one agent.thought_summary (summary mode)."""
                if include_thoughts == "summary" and thought_buf:
                    full = "".join(thought_buf)
                    thought_buf.clear()
                    return [_ev("agent.thought_summary", payload={"text": full})]
                return []

            try:
                async for event in client.prompt_stream(task):
                    # Flush both buffers at every hard turn boundary.
                    if not isinstance(event, (TextDelta, ThoughtDelta)):
                        for ev in _flush_text():
                            yield ev
                        for ev in _flush_thought():
                            yield ev
                    if isinstance(event, TextDelta):
                        if coalesce_messages:
                            text_buf.append(event.text)
                        else:
                            yield _ev("agent.message.delta",
                                      payload={"text": event.text, "channel": "final"})
                    elif isinstance(event, ThoughtDelta):
                        if include_thoughts is False:
                            pass  # drop entirely
                        elif include_thoughts == "delta":
                            yield _ev("agent.thought.delta",
                                      payload={"text": event.text})
                        else:  # "summary" → buffer, flush at the next boundary
                            thought_buf.append(event.text)
                    elif isinstance(event, ToolCallStart):
                        tool_titles[event.tool_use_id] = event.title
                        tool_kinds[event.tool_use_id] = event.kind or ""
                        kind = event.kind or ""
                        inp = event.input
                        path = _input_path(inp)
                        base: dict[str, Any] = {"toolName": event.title}
                        if event.tool_use_id:
                            base["callId"] = event.tool_use_id
                        if inp is not None:
                            base["inputPreview"] = inp
                        # Semantic start by kind (sharper than a generic tool.started;
                        # the gate acts on file.write_requested / command.started).
                        if kind in ("edit", "move", "delete"):
                            pl = {**base, "path": path} if path else base
                            yield _ev("file.write_requested", payload=pl)
                        elif kind == "read":
                            yield _ev("file.read", payload={"path": path} if path else base)
                        elif kind == "execute":
                            cmd = inp.get("command") if isinstance(inp, dict) else None
                            yield _ev("command.started",
                                      payload={**({"command": cmd} if cmd else {}), **base})
                        elif kind == "search":
                            yield _ev("search.query", payload=base)
                        elif kind == "fetch":
                            yield _ev("fetch.requested", payload=base)
                        else:
                            yield _ev("tool.started", payload=base)
                    elif isinstance(event, ToolCallUpdate):
                        if event.status in (ToolStatus.COMPLETED, ToolStatus.FAILED):
                            ok = event.status == ToolStatus.COMPLETED
                            tid = event.tool_use_id
                            kind = tool_kinds.get(tid, "")
                            # Semantic completion: file edits surface as file.edited
                            # (from locations) so the reducer can harvest changed_files.
                            if kind in ("edit", "move", "delete"):
                                for p in _paths_from(event.locations):
                                    yield _ev("file.edited",
                                              payload={"path": p, "ok": ok})
                            payload = {
                                "toolName": tool_titles.get(tid, ""),
                                "ok": ok,
                            }
                            if tid:
                                payload["callId"] = tid
                            if event.output:
                                payload["outputPreview"] = event.output
                            yield _ev("tool.completed", payload=payload)
                        else:
                            yield _ev("agent.update", payload=to_record(event))
                    elif isinstance(event, Done):
                        saw_terminal = True
                        sr = event.stop_reason
                        if sr == "refusal":
                            yield _ev("run.failed", payload={
                                "code": "refusal", "message": "agent refused",
                                "retryable": False,
                            })
                        elif sr == "cancelled":
                            yield _ev("run.cancelled")
                        else:
                            yield _ev("run.completed")
                    elif isinstance(event, Plan):
                        typ = "agent.plan.updated" if seen_plan else "agent.plan.created"
                        seen_plan = True
                        yield _ev(typ, payload={"entries": event.entries})
                    elif isinstance(event, Usage):
                        yield _ev("budget.updated",
                                  payload={
                                      "used": event.used,
                                      "size": event.size,
                                      "cost_amount": event.cost_amount,
                                      "cost_currency": event.cost_currency,
                                  })
                    elif isinstance(event, AvailableCommands):
                        yield _ev("agent.commands.available",
                                  payload={"commands": event.commands})
                    elif isinstance(event, ModeChange):
                        yield _ev("agent.mode.changed", payload={"modeId": event.mode_id})
                    elif isinstance(event, ConfigUpdate):
                        yield _ev("agent.config.updated", payload={"config": event.config})
                    elif isinstance(event, SessionInfo):
                        yield _ev("session.info", payload={
                            "title": event.title, "updatedAt": event.updated_at})
                    elif isinstance(event, RateLimit):
                        yield _ev("rate.limited", payload={
                            "status": event.status, "resetsAt": event.resets_at,
                            "utilization": event.utilization})
                    else:
                        yield _ev("agent.update", payload=to_record(event))
            except ConduitError as exc:
                saw_terminal = True
                yield _ev("run.failed", payload={
                    "code": "agent_error",
                    "message": str(exc),
                    "retryable": False,
                })

            # Flush any buffered final message + reasoning before the terminal.
            for ev in _flush_text():
                yield ev
            for ev in _flush_thought():
                yield ev

            # Stream ended without a terminal event → emit completed.
            if not saw_terminal:
                yield _ev("run.completed")

    return _AcpAdapter()


def process_adapter(
    command: list[str],
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    source: str = "adapter",
) -> Adapter:
    """Adapter that spawns a subprocess emitting newline-delimited JSON events.

    Each non-empty stdout line is parsed as JSON ``{type, summary?, payload?}``
    and yielded as an :class:`AgentEvent`. Non-JSON lines and stderr are
    ignored. A leading ``run.started`` is synthesized (the process's own
    ``run.started``, if any, is dropped to avoid duplication); if the process
    exits without emitting a terminal, ``run.completed`` (rc 0) or
    ``run.failed`` (non-zero rc) is synthesized.
    """

    class _ProcessAdapter:
        name = "process"

        async def run(
            self, task: str, *, run_id: str
        ) -> AsyncIterator[AgentEvent]:
            def _ev(type_: str, *, src: str = source,
                    summary: str | None = None,
                    payload: dict | None = None) -> AgentEvent:
                return AgentEvent(
                    id=_new_id(), type=type_, run_id=run_id, sequence=0,
                    timestamp=_utcnow(), source=src, redaction_status="none",
                    summary=summary, payload=payload,
                )

            yield _ev("run.started", src="sdk", summary="Process started")
            saw_terminal = False
            proc = await asyncio.create_subprocess_exec(
                *command, cwd=cwd, env=env,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            try:
                assert proc.stdout is not None
                async for raw_line in proc.stdout:
                    line = raw_line.decode("utf-8", "replace").strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        continue  # ignore non-JSON lines
                    type_ = obj.get("type")
                    if not type_ or not isinstance(type_, str):
                        continue
                    if type_ == "run.started":
                        continue  # already synthesized
                    if type_ in _TERMINAL_TYPES:
                        saw_terminal = True
                    yield _ev(type_, summary=obj.get("summary"),
                              payload=obj.get("payload"))
            finally:
                # Drain stderr (avoid pipe deadlock) and reap the process.
                if proc.stderr is not None:
                    await proc.stderr.read()
                rc = await proc.wait()

            if not saw_terminal:
                if rc == 0:
                    yield _ev("run.completed")
                else:
                    yield _ev("run.failed", payload={
                        "code": "process_failed",
                        "message": f"exit {rc}",
                        "retryable": False,
                    })

    return _ProcessAdapter()


class Run:
    """A bounded execution that produces a normalized event stream.

    Single-consumer queue driver (A2): a background pump task drains the
    adapter onto an :class:`asyncio.Queue`; the timeout watcher and
    :meth:`cancel` push synthetic events on the same queue; one consumer loop
    dequeues and runs the pure reducer :func:`step`. Only the consumer calls
    ``step`` (no sequence races), and because the consumer reads the queue
    (not a blocking adapter pull), side-task events surface even while the
    adapter awaits the agent — this is what makes the live-ACP approval path
    deadlock-free (Phase 8).
    """

    def __init__(
        self,
        run_id: str,
        adapter: Adapter,
        task: str,
        agent: Agent,
        *,
        clock: Callable[[], float] = time.monotonic,
        id_factory: Callable[[], str] = _new_id,
        policy: Any = None,
        on_approval: Any = None,
        redaction: Any = None,
        approval_timeout_s: float = 300.0,
        on_elicitation: Any = None,
        elicitation_timeout_s: float = 300.0,
        timeout: float | None = None,
    ) -> None:
        self._run_id = run_id
        self._adapter = adapter
        self._task = task
        self._agent = agent
        self._clock = clock
        self._id_factory = id_factory
        self._policy = policy
        self._on_approval = on_approval
        self._redaction = redaction
        self._approval_timeout_s = approval_timeout_s
        self._on_elicitation = on_elicitation
        self._elicitation_timeout_s = elicitation_timeout_s
        self._state = RunState(run_id=run_id)
        if timeout is not None:
            self._state.deadline = clock() + timeout
        # Policy budget (A5): max_runtime_s → deadline, max_cost_usd → cost limit.
        # An explicit *timeout* takes precedence over the budget's runtime limit.
        budget = getattr(policy, "budget", None) if policy is not None else None
        if budget:
            if timeout is None and budget.get("max_runtime_s") is not None:
                self._state.deadline = clock() + float(budget["max_runtime_s"])
            if budget.get("max_cost_usd") is not None:
                self._state.cost_limit = float(budget["max_cost_usd"])
        self._queue: asyncio.Queue = asyncio.Queue()
        self._buffer: list[AgentEvent] = []
        self._result_cache: Result | None = None
        self._pump_task: asyncio.Task | None = None
        self._timeout_task: asyncio.Task | None = None
        self._pump_error: BaseException | None = None
        self._pending_approvals: dict[str, asyncio.Future] = {}
        self._approval_tasks: list[asyncio.Task] = []
        self._deferred: list[AgentEvent] = []
        self._permission_requests: list[tuple[Action, "asyncio.Future[Any]"]] = []
        self._pending_elicitations: dict[str, "asyncio.Future[Any]"] = {}
        self._elicitation_requests: list[tuple[Any, "asyncio.Future[Any]"]] = []
        self._started = False

    @property
    def run_id(self) -> str:
        """Unique identifier for this run."""
        return self._run_id

    @property
    def agent(self) -> Agent:
        """The agent configuration this run was started with."""
        return self._agent

    def status(self) -> str:
        """Current status (running | completed | failed | cancelled | timed_out)."""
        return self._state.status

    # -- internals ----------------------------------------------------------

    def _ensure_started(self) -> None:
        if self._started:
            return
        self._started = True
        self._pump_task = asyncio.create_task(self._pump())
        if self._state.deadline is not None:
            delay = max(0.0, self._state.deadline - self._clock())
            self._timeout_task = asyncio.create_task(self._timeout_watcher(delay))

    async def _pump(self) -> None:
        """Drain the adapter onto the queue; signal exhaustion with _DRAIN."""
        try:
            async for raw in self._adapter.run(self._task, run_id=self._run_id):
                if self._redaction is not None:
                    raw = redact_event(raw, self._redaction)
                await self._queue.put(raw)
        except asyncio.CancelledError:
            pass
        except BaseException as exc:  # noqa: BLE001 — re-raised by the consumer
            self._pump_error = exc
        finally:
            await self._queue.put(_DRAIN)

    async def _timeout_watcher(self, delay: float) -> None:
        """Sleep until the deadline, then enqueue a tick for the reducer to recheck."""
        try:
            await asyncio.sleep(delay)
            await self._queue.put(_TICK)
        except asyncio.CancelledError:
            pass

    def _cleanup_tasks(self) -> None:
        if self._pump_task and not self._pump_task.done():
            self._pump_task.cancel()
        if self._timeout_task and not self._timeout_task.done():
            self._timeout_task.cancel()
        for t in self._approval_tasks:
            if not t.done():
                t.cancel()

    def _process(self, raw: AgentEvent | None) -> list[AgentEvent]:
        emitted = step(
            self._state, raw,
            now=self._clock(), new_id=self._id_factory, policy=self._policy,
        )
        self._buffer.extend(emitted)
        return emitted

    async def _handle_pending_approval(self) -> None:
        """After the reducer sets a pending approval, resolve it.

        With *on_approval* set, call it (sync or coroutine) on the consumer
        task and enqueue the synthetic resolution. Without it, register an
        external Future + an approval-timeout watcher so :meth:`approve` /
        :meth:`reject` (or expiry) can settle it. Runs entirely on the single
        consumer task — never blocks the adapter or client loops.
        """
        if self._state.pending_approval is None:
            return
        if self._on_approval is not None:
            await self._resolve_via_callback()
        else:
            self._setup_external_approval()

    def _setup_external_approval(self) -> None:
        approval_id = self._state.pending_approval[0]
        if approval_id in self._pending_approvals:
            return  # already armed
        loop = asyncio.get_event_loop()
        self._pending_approvals[approval_id] = loop.create_future()
        self._approval_tasks.append(asyncio.create_task(
            self._approval_timeout_watcher(approval_id, self._approval_timeout_s)
        ))

    async def _resolve_via_callback(self) -> None:
        approval_id, _gated, action = self._state.pending_approval  # type: ignore[misc]
        req = ApprovalRequest(
            approval_id=approval_id, kind=action.kind, risk="med",
            action_preview=action.to_record(),
        )
        try:
            decision = self._on_approval(req)
            if inspect.isawaitable(decision):
                decision = await decision
            if not isinstance(decision, ApprovalDecision):
                decision = ApprovalDecision(
                    decision="reject",
                    reason="on_approval did not return an ApprovalDecision",
                )
        except Exception as exc:  # noqa: BLE001 — treat callback errors as reject
            decision = ApprovalDecision(decision="reject", reason=str(exc))
        typ = "approval.approved" if decision.decision == "approve" else "approval.rejected"
        self._queue.put_nowait(self._synth_approval(approval_id, typ, decision))

    async def _approval_timeout_watcher(self, approval_id: str, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        if approval_id in self._pending_approvals:
            self._pending_approvals.pop(approval_id, None)
            self._queue.put_nowait(self._synth_approval(
                approval_id, "approval.expired",
                ApprovalDecision(decision="reject", reason="approval timed out"),
            ))

    def _synth_approval(self, approval_id: str, type_: str,
                        decision: ApprovalDecision) -> AgentEvent:
        return AgentEvent(
            id=self._id_factory(), type=type_, run_id=self._run_id,
            sequence=0, timestamp=_utcnow(), source="sdk", redaction_status="none",
            payload={
                "approvalId": approval_id,
                "reason": decision.reason, "by": decision.by,
            },
        )

    # -- public API ---------------------------------------------------------

    async def _iter_events(self) -> AsyncIterator[AgentEvent]:
        """Single consumer: drain the queue through the reducer, deferring
        non-resolution events while an approval is pending (so an adapter
        that emits the terminal before the approval resolves does not end the
        run prematurely). ``_DRAIN`` is skipped while an approval is pending."""
        self._ensure_started()
        try:
            while not self._state.is_terminal():
                item = await self._queue.get()
                if item is _DRAIN:
                    if self._state.pending_approval is not None:
                        continue  # approval pending — wait for its resolution
                    break
                if item is _TICK:
                    for ev in self._process(None):
                        yield ev
                    continue
                if item is _PERMISSION_REQUEST:
                    # ACP permission bridge: evaluate on this consumer task.
                    action, fut = self._permission_requests.pop(0)
                    try:
                        result, emitted = await self._evaluate_permission(action)
                    except Exception as exc:  # noqa: BLE001
                        result, emitted = PermissionResultDeny(str(exc)), []
                    for ev in emitted:
                        yield ev
                    if not fut.done():
                        fut.set_result(result)
                    continue
                if item is _ELICITATION_REQUEST:
                    # ACP elicitation bridge: surface the question + resolve here.
                    req, eid, fut = self._elicitation_requests.pop(0)
                    try:
                        resp, emitted = await self._evaluate_elicitation(req, eid, fut)
                    except Exception as exc:  # noqa: BLE001
                        resp, emitted = ElicitationResponse(action="cancel"), []
                    for ev in emitted:
                        yield ev
                    if not fut.done():
                        fut.set_result(resp)
                    continue
                if (self._state.pending_approval is not None
                        and item.type not in _APPROVAL_RESOLUTION_TYPES):
                    self._deferred.append(item)
                    continue
                for ev in self._process(item):
                    yield ev
                if self._state.pending_approval is not None:
                    await self._handle_pending_approval()
                elif self._deferred:
                    # Approval just resolved — replay deferred events.
                    for d in self._deferred:
                        self._queue.put_nowait(d)
                    self._deferred = []
        finally:
            self._cleanup_tasks()
        if self._pump_error is not None and not self._state.is_terminal():
            raise self._pump_error
        if self._result_cache is None:
            self._result_cache = finalize(self._state)

    async def events(self) -> AsyncIterator[AgentEvent]:
        """Yield every event with monotonically increasing sequence numbers."""
        async for ev in self._iter_events():
            yield ev

    async def result(self) -> Result:
        """Consume all remaining events and return the computed ``Result``.

        Safe to call without iterating :meth:`events`, after partial
        iteration, or once already fully consumed (cached).
        """
        if self._result_cache is not None:
            return self._result_cache
        async for _ in self._iter_events():
            pass
        if self._pump_error is not None and not self._state.is_terminal():
            raise self._pump_error
        return self._result_cache  # type: ignore[return-value]

    async def cancel(self, reason: str | None = None) -> None:
        """Request cancellation: enqueue ``run.cancelling`` + ``run.cancelled``
        (which the reducer sequences + applies) and stop the pump. Idempotent."""
        if self._state.is_terminal():
            return
        ts = _utcnow()
        self._queue.put_nowait(AgentEvent(
            id=self._id_factory(), type="run.cancelling", run_id=self._run_id,
            sequence=0, timestamp=ts, source="sdk", redaction_status="none",
            summary=reason,
        ))
        self._queue.put_nowait(AgentEvent(
            id=self._id_factory(), type="run.cancelled", run_id=self._run_id,
            sequence=0, timestamp=ts, source="sdk", redaction_status="none",
            summary=reason or "Run cancelled",
        ))
        if self._pump_task and not self._pump_task.done():
            self._pump_task.cancel()

    async def approve(self, approval_id: str, *, reason: str | None = None,
                      approved_by: str | None = None) -> None:
        """Resolve a pending external approval as approved. Idempotent."""
        fut = self._pending_approvals.pop(approval_id, None)
        if fut is None:
            raise ValueError(f"no pending approval {approval_id!r}")
        dec = ApprovalDecision(decision="approve", reason=reason, by=approved_by)
        if not fut.done():
            fut.set_result(dec)
        self._queue.put_nowait(self._synth_approval(approval_id, "approval.approved", dec))

    async def reject(self, approval_id: str, *, reason: str | None = None,
                     rejected_by: str | None = None) -> None:
        """Resolve a pending external approval as rejected. Idempotent."""
        fut = self._pending_approvals.pop(approval_id, None)
        if fut is None:
            raise ValueError(f"no pending approval {approval_id!r}")
        dec = ApprovalDecision(decision="reject", reason=reason, by=rejected_by)
        if not fut.done():
            fut.set_result(dec)
        self._queue.put_nowait(self._synth_approval(approval_id, "approval.rejected", dec))

    # -- Elicitation bridge (Slice 2) -------------------------------------

    async def respond(self, elicitation_id: str, content: dict | None = None) -> None:
        """Answer a pending elicitation externally (accept). Idempotent."""
        fut = self._pending_elicitations.pop(elicitation_id, None)
        if fut is None:
            raise ValueError(f"no pending elicitation {elicitation_id!r}")
        resp = ElicitationResponse(action="accept", content=content)
        if not fut.done():
            fut.set_result(resp)
        self._queue.put_nowait(self._synth_elicitation(
            elicitation_id, "elicitation.responded", resp))

    async def cancel_elicitation(self, elicitation_id: str) -> None:
        """Cancel a pending elicitation externally. Idempotent."""
        fut = self._pending_elicitations.pop(elicitation_id, None)
        if fut is None:
            raise ValueError(f"no pending elicitation {elicitation_id!r}")
        resp = ElicitationResponse(action="cancel")
        if not fut.done():
            fut.set_result(resp)
        self._queue.put_nowait(self._synth_elicitation(
            elicitation_id, "elicitation.cancelled", resp))

    def _synth_elicitation(self, eid: str, type_: str,
                           resp: "ElicitationResponse") -> AgentEvent:
        return AgentEvent(
            id=self._id_factory(), type=type_, run_id=self._run_id,
            sequence=0, timestamp=_utcnow(), source="sdk", redaction_status="none",
            payload={"elicitationId": eid, "action": resp.action,
                     "content": resp.content},
        )

    async def _elicitation_timeout_watcher(self, eid: str, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
        except asyncio.CancelledError:
            return
        if eid in self._pending_elicitations:
            self._pending_elicitations.pop(eid, None)
            resp = ElicitationResponse(action="cancel")
            self._queue.put_nowait(self._synth_elicitation(
                eid, "elicitation.expired", resp))

    async def _resolve_elicitation(self, req: Any) -> Any:
        """Called from the ACP elicitation_handler bridge (on the Client's task).
        Enqueues the request + awaits a Future resolved by the consumer."""
        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        eid = req.elicitation_id or self._id_factory()
        self._elicitation_requests.append((req, eid, fut))
        await self._queue.put(_ELICITATION_REQUEST)
        return await fut

    async def _evaluate_elicitation(self, req: Any, eid: str, fut: Any) -> tuple[Any, list[AgentEvent]]:
        """Surface elicitation.requested, resolve via on_elicitation or external,
        emit the resolution event. Returns (ElicitationResponse, events_to_yield)."""
        emitted: list[AgentEvent] = []
        msg = getattr(req, "message", "")
        emitted.extend(self._process(self._mk_event(
            "elicitation.requested",
            payload={"elicitationId": eid, "message": msg,
                     "mode": getattr(req, "mode", "form"),
                     "requestedSchema": getattr(req, "requested_schema", None)},
        )))
        if self._on_elicitation is not None:
            try:
                resp = self._on_elicitation(req)
                if inspect.isawaitable(resp):
                    resp = await resp
                if not isinstance(resp, ElicitationResponse):
                    resp = ElicitationResponse(action="decline")
            except Exception as exc:  # noqa: BLE001
                resp = ElicitationResponse(action="decline")
            typ = "elicitation.cancelled" if resp.action == "cancel" else "elicitation.responded"
            emitted.extend(self._process(self._synth_elicitation(eid, typ, resp)))
            return resp, emitted
        # External: arm the bridge Future + timeout; drain until resolved.
        self._pending_elicitations[eid] = fut
        self._approval_tasks.append(asyncio.create_task(
            self._elicitation_timeout_watcher(eid, self._elicitation_timeout_s)))
        while eid in self._pending_elicitations and not self._state.is_terminal():
            inner = await self._queue.get()
            if inner is _DRAIN:
                continue
            if inner is _TICK or inner is _PERMISSION_REQUEST or inner is _ELICITATION_REQUEST:
                if inner is _TICK:
                    emitted.extend(self._process(None))
                continue
            if (self._state.pending_approval is not None
                    and inner.type not in _APPROVAL_RESOLUTION_TYPES):
                self._deferred.append(inner)
                continue
            emitted.extend(self._process(inner))
            if self._state.pending_approval is not None:
                await self._handle_pending_approval()
            elif self._deferred:
                for d in self._deferred:
                    self._queue.put_nowait(d)
                self._deferred = []
        # Resolve from the bridge future (set by respond/cancel_elicitation/timeout).
        self._pending_elicitations.pop(eid, None)
        if fut.done():
            return fut.result(), emitted
        return ElicitationResponse(action="cancel"), emitted

    # -- ACP permission bridge (P8) ----------------------------------------

    def _mk_event(self, type_: str, *, payload: dict | None = None,
                  summary: str | None = None) -> AgentEvent:
        """Build a raw (sequence-0) synthetic event for the reducer to sequence."""
        return AgentEvent(
            id=self._id_factory(), type=type_, run_id=self._run_id, sequence=0,
            timestamp=_utcnow(), source="sdk", redaction_status="none",
            summary=summary, payload=payload,
        )

    async def _resolve_permission(self, action: Action) -> Any:
        """Called from the ACP ``can_use_tool`` bridge (on the Client's task).

        Enqueues a permission decision request on the run queue and awaits a
        Future. The single consumer task evaluates the policy and resolves it
        — so the live-ACP approval path cannot deadlock (the consumer reads
        the queue, not a blocked adapter pull).
        """
        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._permission_requests.append((action, fut))
        await self._queue.put(_PERMISSION_REQUEST)
        return await fut

    async def _evaluate_permission(
        self, action: Action
    ) -> tuple[Any, list[AgentEvent]]:
        """Evaluate a permission request on the consumer task. Returns
        ``(PermissionResult, events_to_yield)``."""
        emitted: list[AgentEvent] = []
        if self._policy is None:
            return PermissionResultAllow(), emitted
        decision = self._policy.evaluate(action)
        if decision.effect == "allow":
            return PermissionResultAllow(), emitted
        if decision.effect == "deny":
            emitted.extend(self._process(self._mk_event(
                "policy.blocked",
                payload={"reason": decision.reason or "denied",
                         "action": action.to_record()},
            )))
            return PermissionResultDeny(decision.reason or "denied"), emitted
        # require_approval: emit approval.required, set pending (no gated event),
        # resolve via the approval machinery, then drain until resolved.
        approval_id = self._id_factory()
        emitted.extend(self._process(self._mk_event(
            "approval.required",
            payload={"approvalId": approval_id, "kind": action.kind,
                     "reason": decision.reason or "approval required",
                     "risk": decision.risk, "actionPreview": action.to_record()},
        )))
        self._state.pending_approval = (approval_id, None, action)
        if self._on_approval is not None:
            await self._resolve_via_callback()
        else:
            self._setup_external_approval()
        # Drain until the approval resolves (nested consume, same task).
        while (self._state.pending_approval is not None
               and not self._state.is_terminal()):
            inner = await self._queue.get()
            if inner is _DRAIN:
                continue
            if inner is _TICK or inner is _PERMISSION_REQUEST:
                if inner is _TICK:
                    emitted.extend(self._process(None))
                continue
            if (self._state.pending_approval is not None
                    and inner.type not in _APPROVAL_RESOLUTION_TYPES):
                self._deferred.append(inner)
                continue
            emitted.extend(self._process(inner))
            if self._state.pending_approval is not None:
                await self._handle_pending_approval()
            elif self._deferred:
                for d in self._deferred:
                    self._queue.put_nowait(d)
                self._deferred = []
        aps = self._state.evidence.approvals
        if aps and aps[-1].get("decision") == "approved":
            return PermissionResultAllow(), emitted
        return PermissionResultDeny("approval rejected"), emitted


_DRAIN: Any = object()  # sentinel: the adapter stream is exhausted
_TICK: Any = object()   # sentinel: the timeout watcher fired (recheck deadline)
_PERMISSION_REQUEST: Any = object()  # sentinel: the ACP permission bridge needs a decision
_ELICITATION_REQUEST: Any = object()  # sentinel: the ACP elicitation bridge needs an answer
_APPROVAL_RESOLUTION_TYPES = frozenset({
    "approval.approved", "approval.rejected", "approval.expired",
})


class Runner:
    """Factory for creating and starting :class:`Run` instances."""

    @classmethod
    async def start(
        cls,
        agent: Agent,
        *,
        task: str,
        adapter: Adapter,
        policy: Any = None,
        on_approval: Any = None,
        redaction: Any = None,
        approval_timeout_s: float = 300.0,
        on_elicitation: Any = None,
        elicitation_timeout_s: float = 300.0,
        timeout: float | None = None,
        clock: Callable[[], float] = time.monotonic,
        id_factory: Callable[[], str] = _new_id,
    ) -> Run:
        """Create a new :class:`Run` wired to the given *adapter*.

        *timeout* (seconds) sets a runtime budget enforced inside the reducer
        (no ``asyncio.wait_for``). *policy* / *on_approval* / *redaction* are
        wired in Phases 3-5; they are accepted now so the signature is stable.
        """
        run_id = _new_id()
        run = Run(
            run_id=run_id, adapter=adapter, task=task, agent=agent,
            clock=clock, id_factory=id_factory, policy=policy,
            on_approval=on_approval, redaction=redaction,
            approval_timeout_s=approval_timeout_s,
            on_elicitation=on_elicitation,
            elicitation_timeout_s=elicitation_timeout_s,
            timeout=timeout,
        )
        # Wire the run into adapters that need it (the ACP permission bridge).
        bind = getattr(adapter, "bind_run", None)
        if callable(bind):
            bind(run)
        return run

    @classmethod
    async def run(
        cls,
        agent: Agent,
        *,
        task: str,
        adapter: Adapter,
        **kw: Any,
    ) -> Result:
        """Start a run and drain it to a :class:`Result` (convenience)."""
        run = await cls.start(agent, task=task, adapter=adapter, **kw)
        return await run.result()


async def query(
    prompt: str,
    *,
    adapter: Adapter,
    agent: Agent | None = None,
    policy: Any = None,
    redaction: Any = None,
    on_approval: Any = None,
) -> AsyncIterator[AgentEvent]:
    """Claude-style streaming sugar: start a run and yield its events.

    Thin wrapper over :meth:`Runner.start` + :meth:`Run.events`. Not exported
    at top level (``conduit_sdk.query`` is the ACP-level ``activate.query``);
    use as ``conduit_sdk.runlayer.query``.
    """
    run = await Runner.start(
        agent or Agent(name="query"), task=prompt, adapter=adapter,
        policy=policy, redaction=redaction, on_approval=on_approval,
    )
    async for ev in run.events():
        yield ev


def conductor_adapter(
    base_command: list[str],
    chain: Any,
    *,
    conductor: str = "agent-client-protocol-conductor",
    **client_kwargs: Any,
) -> Adapter:
    """Adapter that runs *base_command* behind an ACP conductor proxy *chain*.

    Builds the conductor argv via :func:`proxy.conductor_command` (raises
    :class:`ProxyError` on an empty chain), owns the :class:`Client` lifecycle,
    and delegates to :func:`acp_adapter`. Public surface is identical to
    :func:`acp_adapter`.
    """
    from conduit_sdk import Client
    from conduit_sdk.proxy import conductor_command

    argv = conductor_command(base_command, chain, conductor=conductor)

    class _ConductorAdapter:
        name = "conductor"

        async def run(self, task: str, *, run_id: str) -> AsyncIterator[AgentEvent]:
            async with Client(argv, **client_kwargs) as client:
                async for ev in acp_adapter(client).run(task, run_id=run_id):
                    yield ev

    return _ConductorAdapter()


def acp_agent(
    spec: "list[str] | str",
    *,
    options: Any = None,
    registry: Any = None,
    timeout: float = 60,
    coalesce_messages: bool = False,
    include_thoughts: bool | str = "summary",
    enable_elicitation: bool = True,
) -> Adapter:
    """Generic local ACP launch adapter (Phase 8).

    *spec* is either a command list (e.g. ``["opencode", "acp"]``) launched
    directly, or a registry id string resolved via
    :meth:`Client.from_registry`. The adapter owns the :class:`Client`
    lifecycle (``async with``) and delegates to :func:`acp_adapter`. It also
    installs a ``can_use_tool`` bridge that routes the agent's permission
    requests through the run's :class:`Policy` / approval machinery (A6) —
    deadlock-free, since the bridge enqueues onto the run queue and the single
    consumer resolves it.

    *enable_elicitation* (default ``True``) advertises the **UNSTABLE** ACP
    elicitation capability so the agent can ask structured questions (omp's ACP
    speaks the unstable wire). Set ``False`` to stay on stable-only ACP. When
    enabled, route answers via ``on_elicitation`` / ``run.respond()``.
    """
    from conduit_sdk import Client
    from conduit_sdk.options import AgentOptions

    class _AcpAgentAdapter:
        name = "acp"

        def __init__(self) -> None:
            self._run: Any = None

        def bind_run(self, run: Run) -> None:
            self._run = run
        def _build_options(self) -> Any:
            base = options if options is not None else AgentOptions()
            # Always inject the permission bridge (stable ACP). The elicitation
            # bridge is UNSTABLE in ACP — only advertise the capability when the
            # caller explicitly opts in (the agent won't elicit otherwise).
            kwargs: dict[str, Any] = {"can_use_tool": self._bridge}
            if enable_elicitation:
                kwargs["elicitation_handler"] = self._elicit_bridge
            return replace(base, **kwargs)

        async def _bridge(self, tool_name: str, tool_input: Any, ctx: Any) -> Any:
            if self._run is None:
                return PermissionResultAllow()
            return await self._run._resolve_permission(
                Action("tool", tool_name=tool_name)
            )

        async def _elicit_bridge(self, req: Any) -> Any:
            if self._run is None:
                from conduit_sdk.elicitation import ElicitationResponse
                return ElicitationResponse(action="decline")
            return await self._run._resolve_elicitation(req)

        async def run(self, task: str, *, run_id: str) -> AsyncIterator[AgentEvent]:
            opts = self._build_options()
            if isinstance(spec, str):
                cm = Client.from_registry(
                    spec, registry=registry, timeout=int(timeout), options=opts,
                )
            else:
                cm = Client(spec, timeout=int(timeout), options=opts)
            async with cm as client:
                async for ev in acp_adapter(
                    client, coalesce_messages=coalesce_messages,
                    include_thoughts=include_thoughts,
                ).run(task, run_id=run_id):
                    yield ev

    return _AcpAgentAdapter()
