"""Run core — pure, sync, data-oriented run-layer primitives (no asyncio/IO).

This module is the Stage-2 port manifest: everything here ports to
``crates/conduit-core`` unchanged in shape. It owns the *core* types
(:class:`AgentEvent`, :class:`Result`), the envelope constants, the id/time
helpers, and the canonical wire-record bridges ``to_record``/``from_record``
(snake_case Python fields ↔ camelCase wire form, per the Background Agent SDK
wire contract — see ``schema/`` and ``docs/seed/.../EVENTS.md``).

Dependency rule (A1): ``runcore`` depends only on the stdlib. The async glue
(``runlayer.Run``/``Runner``/adapters) depends on ``runcore``, never the
reverse. ``runlayer`` re-exports the core symbols so existing import sites stay
valid.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from conduit_sdk.policy import Action

# Sentinel for "attribute absent" (distinct from a None field value). Used by
# the record bridges below; defined first so call-time lookups resolve cleanly.
_UNSET: Any = object()

__all__ = [
    # Constants
    "_VALID_SOURCES",
    "_VALID_REDACTIONS",
    "_VALID_STATUSES",
    "_TERMINAL_TYPES",
    # Helpers
    "_utcnow",
    "_new_id",
    # Core data types
    "AgentEvent",
    "Evidence",
    "Result",
    # Reducer (pure sync; ports to Rust)
    "RunState",
    "step",
    "finalize",
    # Wire-record bridges
    "to_record",
]


# ---------------------------------------------------------------------------
# Envelope constants
# ---------------------------------------------------------------------------

_VALID_SOURCES = frozenset({
    "sdk", "adapter", "proxy", "agent", "sandbox", "controller", "server",
})
_VALID_REDACTIONS = frozenset({"none", "redacted", "blocked", "unknown"})
_VALID_STATUSES = frozenset({"completed", "failed", "cancelled", "timed_out"})
_TERMINAL_TYPES = frozenset({
    "run.completed", "run.failed", "run.cancelled", "run.timed_out",
})


# ---------------------------------------------------------------------------
# Pure helpers (overridable in glue via injection; core stays loop-free)
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    """Return current UTC time as an ISO-8601 string (no microseconds)."""
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    """Return a short unique hex identifier."""
    return uuid4().hex


# ---------------------------------------------------------------------------
# Core data types
# ---------------------------------------------------------------------------


@dataclass
class AgentEvent:
    """Normalized event envelope (per ``docs/seed/.../EVENTS.md``).

    Fields are snake_case in Python; the wire (camelCase) form is produced by
    :func:`to_record`. All fields are required at construction except *summary*
    and *payload*, which default to ``None``.
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
    # Forward-compat envelope (A3): versioned wire + reserved tracing/tenancy ids.
    version: int = 1
    trace_id: str | None = None
    span_id: str | None = None
    workspace_id: str | None = None
    tenant_id: str | None = None


@dataclass
class Result:
    """Final result of a completed run.

    Computed by the reducer (:func:`finalize`) after all events are consumed.
    *status* is one of ``"completed"``, ``"failed"``, ``"cancelled"``, or
    ``"timed_out"``. Evidence fields mirror :class:`Evidence` and the seed wire
    shape (``docs/seed/.../SOURCE.md`` §10); *error* carries the seed
    ``failure`` object (``{code, message, retryable?, diagnosis?}``).
    """

    run_id: str
    status: str
    event_count: int
    summary: str | None = None
    error: dict | None = None
    started_at: str = ""
    ended_at: str = ""
    # Evidence (P1) — flattened seed Result shape; populated by finalize().
    final_output: str | None = None
    changed_files: list[str] = field(default_factory=list)
    diff: str | None = None
    tests: list[dict] = field(default_factory=list)
    approvals: list[dict] = field(default_factory=list)
    pr: dict | None = None
    artifacts: list[dict] = field(default_factory=list)
    questions: list[dict] = field(default_factory=list)
    usage: dict | None = None


@dataclass
class Evidence:
    """Mutable evidence accumulator (core; ports to Rust).

    Updated event-by-event by the reducer ``step``; copied into a :class:`Result`
    by ``finalize``. Field shapes match the seed wire contract (camelCase on the
    wire via :func:`result_to_record`).
    """

    final_output: str | None = None
    changed_files: list[str] = field(default_factory=list)
    diff: str | None = None
    tests: list[dict] = field(default_factory=list)
    approvals: list[dict] = field(default_factory=list)
    pr: dict | None = None
    artifacts: list[dict] = field(default_factory=list)
    questions: list[dict] = field(default_factory=list)
    usage: dict | None = None


# ---------------------------------------------------------------------------
# Reducer (pure, sync, no asyncio) — the Stage-2 port manifest core.
#
# ``step`` is the single mutation point: it takes a raw adapter event (or None
# for a deadline "tick"), assigns the next sequence, updates evidence/status,
# and returns the events to emit. Injected ``now`` (monotonic float) drives the
# deadline check; injected ``new_id`` mints ids for synthetic events. With no
# event loop in sight, this is driven directly by tests/test_runcore.py.
# ---------------------------------------------------------------------------


@dataclass
class RunState:
    """Mutable run state advanced by :func:`step`, frozen into a Result by
    :func:`finalize`. Pure data — no asyncio, no I/O."""

    run_id: str
    seq_counter: int = 0
    status: str = "running"  # running | completed | failed | cancelled | timed_out
    evidence: Evidence = field(default_factory=Evidence)
    started_at: str = ""
    last_timestamp: str = ""
    terminal_summary: str | None = None
    terminal_payload: dict | None = None  # failure object on status=="failed"
    deadline: float | None = None  # monotonic seconds; None = no runtime budget
    cost_limit: float | None = None  # USD; None = no cost budget
    # Approval-gate state (P4): (approval_id, gated_event, action) awaiting a decision.
    pending_approval: tuple[str, AgentEvent, Any] | None = None

    def is_terminal(self) -> bool:
        return self.status != "running"


#camelCase usage keys accepted from cost/budget/usage payloads (both casings).
_USAGE_KEY_MAP: dict[str, str] = {
    "input_tokens": "inputTokens", "output_tokens": "outputTokens",
    "cost_usd": "costUsd", "duration_ms": "durationMs",
    "inputTokens": "inputTokens", "outputTokens": "outputTokens",
    "costUsd": "costUsd", "durationMs": "durationMs",
}


def _collect_evidence(ev: AgentEvent, evidence: Evidence) -> None:
    """Update *evidence* from a sequenced event (pure; reads camelCase payloads).

    Implements the evidence rules from the plan (P2 §1). Unknown payload keys
    are ignored. ``changed_files`` is dedup-preserving-order.
    """
    t = ev.type
    p = ev.payload or {}

    if t == "agent.message.completed":
        # Authoritative full message — resets any partial delta accumulation.
        text = p.get("text")
        if text:
            evidence.final_output = text
    elif t == "agent.message.delta":
        # Streamed token — accumulate final-channel deltas so a token-by-token
        # agent still reconstructs the full final_output (last-wins was a bug).
        if p.get("channel") == "final":
            text = p.get("text")
            if text:
                evidence.final_output = (evidence.final_output or "") + text

    elif t in ("diff.preview_created", "diff.applied"):
        files = p.get("files") or ([p["path"]] if p.get("path") else [])
        for f in files:
            if f not in evidence.changed_files:
                evidence.changed_files.append(f)
        if p.get("diff") is not None:
            evidence.diff = p["diff"]

    elif t == "file.edited":
        path = p.get("path")
        if path and path not in evidence.changed_files:
            evidence.changed_files.append(path)

    elif t == "test.completed":
        evidence.tests.append({
            "command": p.get("command", ""),
            "exitCode": p.get("exitCode", p.get("exit_code", 0)),
            "passed": p.get("passed"),
            "durationMs": p.get("durationMs", p.get("duration_ms")),
            "output": p.get("output"),
        })

    elif t in ("approval.approved", "approval.rejected", "approval.expired"):
        decision = {"approval.approved": "approved",
                    "approval.rejected": "rejected",
                    "approval.expired": "expired"}[t]
        evidence.approvals.append({
            "id": p.get("approvalId", p.get("approval_id", "")),
            "decision": decision,
            "reason": p.get("reason"),
        })

    elif t == "pr.opened":
        evidence.pr = p

    elif t == "artifact.created":
        evidence.artifacts.append(p)

    elif t in ("elicitation.responded", "elicitation.cancelled", "elicitation.expired"):
        # Record the question's resolution into evidence.questions.
        evidence.questions.append({
            "action": p.get("action", t.split(".")[1]),
            "content": p.get("content"),
            "message": p.get("message"),
        })

    elif t in ("cost.updated", "budget.updated", "usage"):
        if evidence.usage is None:
            evidence.usage = {}
        for k, v in p.items():
            ck = _USAGE_KEY_MAP.get(k)
            if ck is not None:
                evidence.usage[ck] = v


_TERMINAL_STATUS_BY_TYPE: dict[str, str] = {
    "run.completed": "completed",
    "run.failed": "failed",
    "run.cancelled": "cancelled",
    "run.timed_out": "timed_out",
}


def _apply_terminal(state: RunState, ev: AgentEvent) -> None:
    """Record the terminal transition from a run.* event."""
    new_status = _TERMINAL_STATUS_BY_TYPE.get(ev.type)
    if new_status is None:
        return
    state.status = new_status
    state.terminal_summary = ev.summary
    if ev.type == "run.failed":
        state.terminal_payload = ev.payload if ev.payload is not None else {
            "code": "unknown", "message": "Run failed",
        }
    else:
        state.terminal_payload = None
    if new_status == "cancelled" and not state.terminal_summary:
        state.terminal_summary = "Run cancelled"
    if new_status == "timed_out" and not state.terminal_summary:
        state.terminal_summary = "Run timed out"


def _with_sequence(raw: AgentEvent, sequence: int) -> AgentEvent:
    """Return a copy of *raw* with the assigned *sequence* (and run-scoped id
    preserved). The reducer never mutates the adapter's event."""
    return AgentEvent(
        id=raw.id, type=raw.type, run_id=raw.run_id, sequence=sequence,
        timestamp=raw.timestamp, source=raw.source,
        redaction_status=raw.redaction_status, summary=raw.summary,
        payload=raw.payload, version=raw.version, trace_id=raw.trace_id,
        span_id=raw.span_id, workspace_id=raw.workspace_id, tenant_id=raw.tenant_id,
    )


def _synth(state: RunState, type_: str, *, new_id: Callable[[], str],
           source: str = "sdk", summary: str | None = None,
           payload: dict | None = None) -> AgentEvent:
    """Mint a synthetic run-scoped event with the next sequence."""
    state.seq_counter += 1
    return AgentEvent(
        id=new_id(), type=type_, run_id=state.run_id, sequence=state.seq_counter,
        timestamp=state.last_timestamp, source=source, redaction_status="none",
        summary=summary, payload=payload,
    )


def _extract_action(ev: AgentEvent) -> Action | None:
    """Extract a gateable :class:`Action` from an event (A6), or None."""
    t = ev.type
    p = ev.payload or {}
    if t == "command.started":
        return Action("command", command=p.get("command"))
    if t == "file.write_requested":
        return Action("file_write", path=p.get("path"))
    if t == "diff.preview_created" and p.get("requiresApproval"):
        files = p.get("files") or []
        return Action("diff_apply", path=",".join(str(f) for f in files) if files else None)
    if t == "tool.started":
        return Action("tool", tool_name=p.get("toolName"))
    return None


def _emit_blocked(state: RunState, raw: AgentEvent, action: Action,
                  reason: str, new_id: Callable[[], str]) -> AgentEvent:
    state.seq_counter += 1
    return AgentEvent(
        id=new_id(), type="policy.blocked", run_id=state.run_id,
        sequence=state.seq_counter, timestamp=raw.timestamp or state.last_timestamp,
        source="sdk", redaction_status="none",
        payload={"reason": reason, "action": action.to_record()},
    )


def _emit_required(state: RunState, raw: AgentEvent, action: Action,
                   decision: Any, new_id: Callable[[], str]) -> AgentEvent:
    approval_id = new_id()
    state.seq_counter += 1
    req = AgentEvent(
        id=new_id(), type="approval.required", run_id=state.run_id,
        sequence=state.seq_counter, timestamp=raw.timestamp or state.last_timestamp,
        source="sdk", redaction_status="none",
        payload={
            "approvalId": approval_id, "kind": action.kind,
            "reason": decision.reason or "approval required", "risk": decision.risk,
            "actionPreview": action.to_record(),
        },
    )
    state.pending_approval = (approval_id, raw, action)
    return req


def _resolve_approval(state: RunState, raw: AgentEvent,
                      new_id: Callable[[], str]) -> list[AgentEvent]:
    """Resolve a pending approval from a synthetic approval.* event."""
    _approval_id, gated_raw, action = state.pending_approval  # type: ignore[misc]
    state.pending_approval = None
    out: list[AgentEvent] = []

    # The resolution event itself (approval.approved/rejected/expired).
    state.seq_counter += 1
    res = _with_sequence(raw, state.seq_counter)
    state.last_timestamp = res.timestamp or state.last_timestamp
    _collect_evidence(res, state.evidence)
    out.append(res)

    if raw.type == "approval.approved":
        # Gated action proceeds. For the permission bridge (gated_raw is None)
        # there is no event to release — the tool call proceeds via PermissionResultAllow.
        if gated_raw is not None:
            out.append(_passthrough(state, gated_raw))
    else:
        reason = ("approval rejected" if raw.type == "approval.rejected"
                  else "approval expired")
        if gated_raw is not None:
            out.append(_emit_blocked(state, gated_raw, action, reason, new_id))
        else:
            # Permission bridge reject: emit policy.blocked with no source event.
            state.seq_counter += 1
            out.append(AgentEvent(
                id=new_id(), type="policy.blocked", run_id=state.run_id,
                sequence=state.seq_counter, timestamp=state.last_timestamp,
                source="sdk", redaction_status="none",
                payload={"reason": reason, "action": action.to_record()},
            ))
    return out


def _passthrough(state: RunState, raw: AgentEvent) -> AgentEvent:
    """Sequence, collect evidence, and apply terminal status for a normal event."""
    state.seq_counter += 1
    ev = _with_sequence(raw, state.seq_counter)
    state.last_timestamp = ev.timestamp or state.last_timestamp
    if ev.type == "run.started" and not state.started_at:
        state.started_at = ev.timestamp
    _collect_evidence(ev, state.evidence)
    _apply_terminal(state, ev)
    return ev


def _emit_timeout(state: RunState, new_id: Callable[[], str]) -> list[AgentEvent]:
    out = [
        _synth(state, "run.cancelling", new_id=new_id, source="sdk"),
        _synth(state, "run.timed_out", new_id=new_id, source="sdk", summary="Run timed out"),
    ]
    state.status = "timed_out"
    state.terminal_summary = "Run timed out"
    state.terminal_payload = None
    return out


def _check_cost(state: RunState, out: list[AgentEvent],
                new_id: Callable[[], str]) -> list[AgentEvent]:
    """Append budget.exceeded + run.failed when cumulative cost reaches the limit (A5)."""
    if state.is_terminal() or state.cost_limit is None:
        return out
    cost = (state.evidence.usage or {}).get("costUsd")
    if cost is None or cost < state.cost_limit:
        return out
    out.append(_synth(state, "budget.exceeded", new_id=new_id,
                      payload={"limit": state.cost_limit, "costUsd": cost}))
    state.seq_counter += 1
    fail_payload = {
        "code": "budget_exceeded",
        "message": f"cost ${cost} reached limit ${state.cost_limit}",
        "retryable": False,
    }
    out.append(AgentEvent(
        id=new_id(), type="run.failed", run_id=state.run_id,
        sequence=state.seq_counter, timestamp=state.last_timestamp,
        source="sdk", redaction_status="none", payload=fail_payload,
    ))
    state.status = "failed"
    state.terminal_summary = "Budget exceeded"
    state.terminal_payload = fail_payload
    return out


def step(state: RunState, raw: AgentEvent | None, *, now: float,
         new_id: Callable[[], str], policy: Any = None) -> list[AgentEvent]:
    """Advance *state* by one raw event (or a deadline tick when *raw* is None).

    Returns the events to emit (sequence-assigned). When *raw* is None only the
    deadline is re-evaluated. Terminal states drop further input. The approval
    gate (A6) evaluates *policy* for gateable events; a pending approval only
    advances on a synthetic ``approval.*`` resolution.
    """
    if state.is_terminal():
        return []

    # Runtime budget: deadline compared in the reducer (no asyncio.wait_for).
    if state.deadline is not None and now >= state.deadline:
        return _emit_timeout(state, new_id)

    if raw is None:
        return []  # tick with no breach

    out: list[AgentEvent]

    # A pending approval only advances on its resolution event.
    if state.pending_approval is not None:
        if raw.type in ("approval.approved", "approval.rejected", "approval.expired"):
            out = _resolve_approval(state, raw, new_id)
            return _check_cost(state, out, new_id)
        return []  # non-resolution events are dropped while approval is pending

    # Gate (A6): evaluate the policy for gateable events.
    if policy is not None:
        action = _extract_action(raw)
        if action is not None:
            decision = policy.evaluate(action)
            if decision.effect == "deny":
                return [_emit_blocked(state, raw, action,
                                       decision.reason or "blocked by policy", new_id)]
            if decision.effect == "require_approval":
                return [_emit_required(state, raw, action, decision, new_id)]
            # allow → fall through to passthrough

    out = [_passthrough(state, raw)]
    return _check_cost(state, out, new_id)


def finalize(state: RunState) -> Result:
    """Freeze *state* into a :class:`Result`. Pure; no wall-clock.

    ``ended_at`` is the last observed event timestamp (deterministic). If the
    run never reached a terminal event, status defaults to ``completed``.
    """
    status = state.status if state.is_terminal() else "completed"
    final_output = state.evidence.final_output
    if final_output is None:
        final_output = state.terminal_summary
    return Result(
        run_id=state.run_id,
        status=status,
        event_count=state.seq_counter,
        summary=state.terminal_summary,
        error=state.terminal_payload if status == "failed" else None,
        started_at=state.started_at,
        ended_at=state.last_timestamp,
        final_output=final_output,
        changed_files=list(state.evidence.changed_files),
        diff=state.evidence.diff,
        tests=list(state.evidence.tests),
        approvals=list(state.evidence.approvals),
        pr=state.evidence.pr,
        artifacts=list(state.evidence.artifacts),
        questions=list(state.evidence.questions),
        usage=state.evidence.usage,
    )


def redact_event(event: AgentEvent, filter: Any) -> AgentEvent:
    """Sync: scrub secrets from *event*.payload + summary (Phase 5).

    Reuses ``redaction._deep_scrub`` (imported locally to avoid the
    core↔glue import cycle: redaction imports AgentEvent from runlayer).
    Sets ``redaction_status="redacted"`` when modified; never mutates the
    input. ``secret.redacted`` is not emitted (signaled via the status),
    keeping the contract event-count intact.
    """
    from conduit_sdk.redaction import _deep_scrub
    payload_value = event.payload
    summary_value = event.summary
    modified = False
    if event.payload is not None:
        payload_value, pm = _deep_scrub(event.payload, filter)
        modified = modified or pm
    if event.summary is not None:
        summary_value, sm = _deep_scrub(event.summary, filter)
        modified = modified or sm
    if not modified:
        return event
    return AgentEvent(
        id=event.id, type=event.type, run_id=event.run_id, sequence=event.sequence,
        timestamp=event.timestamp, source=event.source,
        redaction_status="redacted", summary=summary_value, payload=payload_value,
        version=event.version, trace_id=event.trace_id, span_id=event.span_id,
        workspace_id=event.workspace_id, tenant_id=event.tenant_id,
    )


# ---------------------------------------------------------------------------
# Wire-record bridges (snake_case Python ↔ camelCase wire)
#
# Mirrors conduit_sdk.events.to_record/from_record. The round-trip invariant
# ``from_record(to_record(e)) == e`` is guaranteed by the dataclass ``__eq__``.
#
# Field maps list every (python_field, wire_key) pair. Serialization is SPARSE:
# None-valued fields are omitted from the wire record (matches seed `?` optional
# semantics and Rust `#[serde(skip_serializing_if = "Option::is_none")]`).
# Required fields are non-None by construction. ``from_record`` reconstructs
# from the wire keys present (absent optional → dataclass default, which is None)
# so the round-trip ``from_record(to_record(e)) == e`` holds.
# ---------------------------------------------------------------------------

# AgentEvent: python snake_case → wire camelCase (seed EVENTS.md envelope).
_EVENT_FIELD_MAP: list[tuple[str, str]] = [
    ("id", "id"),
    ("type", "type"),
    ("run_id", "runId"),
    ("sequence", "sequence"),
    ("timestamp", "timestamp"),
    ("source", "source"),
    ("redaction_status", "redactionStatus"),
    ("summary", "summary"),
    ("payload", "payload"),
    # Reserved / forward-compat (A3): emitted once the dataclass gains them.
    ("version", "version"),
    ("trace_id", "traceId"),
    ("span_id", "spanId"),
    ("workspace_id", "workspaceId"),
    ("tenant_id", "tenantId"),
]


def to_record(event: AgentEvent) -> dict[str, Any]:
    """Serialize *event* to a sparse camelCase JSON-safe dict (wire envelope).

    None-valued optional fields are omitted; required fields are always present.
    """
    d: dict[str, Any] = {}
    for py_field, wire_key in _EVENT_FIELD_MAP:
        val = getattr(event, py_field, _UNSET)
        if val is _UNSET or val is None:
            continue
        d[wire_key] = val
    return d


def from_record(record: dict[str, Any]) -> AgentEvent:
    """Deserialize a camelCase wire envelope back to :class:`AgentEvent`.

    MUST satisfy ``from_record(to_record(e)) == e``. Unknown wire keys are
    ignored so newer producers do not break older consumers.
    """
    kwargs: dict[str, Any] = {}
    for py_field, wire_key in _EVENT_FIELD_MAP:
        if wire_key in record:
            kwargs[py_field] = record[wire_key]
    return AgentEvent(**kwargs)


# Result: python snake_case → wire camelCase (seed SOURCE.md §10). The Python
# field ``error`` carries the seed ``failure`` object and is emitted under the
# wire key ``failure`` so Rust/TS/conformance vectors match the seed shape.
_RESULT_FIELD_MAP: list[tuple[str, str]] = [
    ("run_id", "runId"),
    ("status", "status"),
    ("event_count", "eventCount"),
    ("summary", "summary"),
    ("error", "failure"),
    ("started_at", "startedAt"),
    ("ended_at", "endedAt"),
    # Evidence (seed SOURCE.md §10).
    ("final_output", "finalOutput"),
    ("changed_files", "changedFiles"),
    ("diff", "diff"),
    ("tests", "tests"),
    ("approvals", "approvals"),
    ("pr", "pr"),
    ("artifacts", "artifacts"),
    ("questions", "questions"),
    ("usage", "usage"),
]


def result_to_record(result: Result) -> dict[str, Any]:
    """Serialize *result* to a sparse camelCase JSON-safe dict (wire Result).

    None-valued optional fields are omitted (sparse wire convention).
    """
    d: dict[str, Any] = {}
    for py_field, wire_key in _RESULT_FIELD_MAP:
        val = getattr(result, py_field, _UNSET)
        if val is _UNSET or val is None:
            continue
        d[wire_key] = val
    return d


def result_from_record(record: dict[str, Any]) -> Result:
    """Deserialize a camelCase wire Result back to :class:`Result`.

    MUST satisfy ``result_from_record(result_to_record(r)) == r``.
    """
    kwargs: dict[str, Any] = {}
    for py_field, wire_key in _RESULT_FIELD_MAP:
        if wire_key in record:
            kwargs[py_field] = record[wire_key]
    return Result(**kwargs)


