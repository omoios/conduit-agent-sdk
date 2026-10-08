"""Reducer-driven conformance tests (Phase 12).

Drives EVERY conformance vector through the pure sync reducer (step/finalize)
with a seeded new_id counter. For ``approval_reject`` vectors, feeds a
synthetic ``approval.rejected`` event inline — right after the gated event
triggers a pending approval — so the terminal ``run.completed`` in the input
does not get dropped. For ``timeout`` vectors, feeds a raw=None tick with
``now > deadline``. Then asserts the wire result is schema-valid and matches
the vector's expected structural fields (status, finalOutput, changedFiles,
tests, approvals, etc.).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from conduit_sdk.policy import (
    require_approval_for,
    max_runtime_minutes,
)
from conduit_sdk.runcore import (
    RunState,
    AgentEvent,
    step,
    finalize,
    from_record,
    result_to_record,
)
from conduit_sdk.tools import _validate_against_schema

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = REPO_ROOT / "schema"
VECTOR_DIR = Path(__file__).resolve().parent / "vectors"

# Use a monotonic clock that is safely below any deadline in the timeout
# vector; the timeout tick uses a much larger value.
SAFE_NOW = 0.0
TIMEOUT_TICK = 99999.0


def _load_schema(name: str) -> dict[str, Any]:
    with (SCHEMA_DIR / name).open() as fh:
        return json.load(fh)


def _load_vectors() -> list[dict[str, Any]]:
    files = sorted(VECTOR_DIR.glob("*.json"))
    out: list[dict[str, Any]] = []
    for f in files:
        with f.open() as fh:
            data = json.load(fh)
            if isinstance(data, list):
                out.extend(data)
            else:
                out.append(data)
    return out


_RESULT_SCHEMA = _load_schema("result.schema.json")
_VECTORS = _load_vectors()


def _counter_id() -> Callable[[], str]:
    """Seeded id factory: id_1, id_2, ... for deterministic output."""
    i: int = 0

    def factory() -> str:
        nonlocal i
        i += 1
        return f"id_{i}"

    return factory


def _resolve_policy(vector: dict[str, Any]) -> Any:
    """Map the vector's ``policy`` field (if any) to a Policy instance."""
    p = vector.get("policy")
    if p is None:
        return None
    kind = p.get("kind")
    if kind == "require_approval_for":
        return require_approval_for(
            commands=p.get("commands", []),
            files=p.get("files", []),
        )
    if kind == "max_runtime_minutes":
        return max_runtime_minutes(p.get("minutes", 1))
    return None


# ---------------------------------------------------------------------------
# Shared helpers for structural comparison
# ---------------------------------------------------------------------------


def _compare_tests(
    actual: list[dict[str, Any]],
    expected: list[dict[str, Any]],
    name: str,
) -> None:
    assert len(actual) == len(expected), (
        f"{name}: tests length {len(actual)} != expected {len(expected)}"
    )
    for i, (exp_t, act_t) in enumerate(zip(expected, actual)):
        for k in ("command", "exitCode", "passed", "durationMs", "output"):
            if k in exp_t:
                assert act_t.get(k) == exp_t[k], (
                    f"{name}: tests[{i}].{k}: expected {exp_t[k]!r}, got {act_t.get(k)!r}"
                )


def _compare_approvals(
    actual: list[dict[str, Any]],
    expected: list[dict[str, Any]],
    name: str,
) -> None:
    """Compare approval decisions only (ids are run-scoped)."""
    assert len(actual) == len(expected), (
        f"{name}: approvals length {len(actual)} != expected {len(expected)}"
    )
    for i, (exp_a, act_a) in enumerate(zip(expected, actual)):
        # decisions must match
        assert act_a.get("decision") == exp_a.get("decision"), (
            f"{name}: approvals[{i}].decision: expected {exp_a.get('decision')!r}, "
            f"got {act_a.get('decision')!r}"
        )
        # reason: when present in expected, compare; otherwise skip
        if "reason" in exp_a:
            assert act_a.get("reason") == exp_a["reason"], (
                f"{name}: approvals[{i}].reason: expected {exp_a['reason']!r}, "
                f"got {act_a.get('reason')!r}"
            )


def _compare_result(
    result_record: dict[str, Any],
    expected: dict[str, Any],
    name: str,
) -> None:
    """Structural comparison: stable fields, ignore run-scoped ids/timestamps."""

    # status, finalOutput, summary
    for key in ("status", "finalOutput", "summary"):
        if key in expected:
            assert result_record.get(key) == expected[key], (
                f"{name}: {key}: expected {expected[key]!r}, got {result_record.get(key)!r}"
            )

    # changedFiles (deterministic list)
    assert result_record.get("changedFiles", []) == expected.get("changedFiles", []), (
        f"{name}: changedFiles mismatch: expected {expected.get('changedFiles')}, "
        f"got {result_record.get('changedFiles')}"
    )

    # tests
    _compare_tests(
        result_record.get("tests", []), expected.get("tests", []), name
    )

    # approvals (compare decisions, not ids)
    _compare_approvals(
        result_record.get("approvals", []), expected.get("approvals", []), name
    )

    # artifacts length
    assert len(result_record.get("artifacts", [])) == len(
        expected.get("artifacts", [])
    ), f"{name}: artifacts length mismatch"

    # eventCount (deterministic with seeded ids)
    assert result_record.get("eventCount") == expected.get("eventCount"), (
        f"{name}: eventCount: expected {expected.get('eventCount')}, "
        f"got {result_record.get('eventCount')}"
    )

    # diff (nullable, deterministic)
    if "diff" in expected:
        assert result_record.get("diff") == expected["diff"], (
            f"{name}: diff: expected {expected['diff']!r}, got {result_record.get('diff')!r}"
        )

    # usage (nullable, deterministic)
    if "usage" in expected:
        assert result_record.get("usage") == expected["usage"], (
            f"{name}: usage mismatch"
        )

    # pr (nullable, deterministic)
    if "pr" in expected:
        assert result_record.get("pr") == expected["pr"], (
            f"{name}: pr mismatch"
        )

    # failure/error (nullable, deterministic)
    if "failure" in expected:
        assert result_record.get("failure") == expected["failure"], (
            f"{name}: failure mismatch"
        )


# ---------------------------------------------------------------------------
# Conformance test
# ---------------------------------------------------------------------------


class TestConformance:
    """Every conformance vector driven through the pure sync reducer."""

    def test_all_vectors_conform(self) -> None:
        pending: list[tuple[str, str | None]] = []
        for vector in _VECTORS:
            name = vector["name"]
            try:
                self._check_vector(vector)
                pending.append((name, None))
            except AssertionError as exc:
                pending.append((name, str(exc)))
        # Report all failures at once.
        failures = [(n, m) for n, m in pending if m is not None]
        if failures:
            lines = "\n".join(f"  {n}: {m}" for n, m in failures)
            raise AssertionError(
                f"{len(failures)} vector(s) failed conformance:\n{lines}"
            )

    # -- per-vector runner ------------------------------------------------

    @staticmethod
    def _feed_input_events(
        state: RunState,
        records: list[dict[str, Any]],
        nid: Callable[[], str],
        policy: Any,
    ) -> None:
        """Feed every input record through ``step``. For vectors whose
        policy gates an event (e.g. ``approval_reject``), resolve the
        resulting pending approval inline with a synthetic
        ``approval.rejected`` so subsequent input events (like
        ``run.completed``) are not dropped."""
        for record in records:
            ev = from_record(record)
            step(state, ev, now=SAFE_NOW, new_id=nid, policy=policy)

            # If this event triggered a pending approval, resolve it
            # immediately so the next input event is not silently dropped.
            if state.pending_approval is not None:
                TestConformance._feed_approval_rejected(state, nid)

    @staticmethod
    def _feed_approval_rejected(
        state: RunState,
        nid: Callable[[], str],
    ) -> None:
        """Feed a synthetic ``approval.rejected`` event to resolve the
        currently pending approval."""
        approval_id = state.pending_approval[0]
        rejected_raw = AgentEvent(
            id="x",
            type="approval.rejected",
            run_id=state.run_id,
            sequence=0,
            timestamp="TR",
            source="sdk",
            redaction_status="none",
            payload={"approvalId": approval_id},
        )
        step(state, rejected_raw, now=SAFE_NOW, new_id=nid)

    # -- check ------------------------------------------------------------

    def _check_vector(self, vector: dict[str, Any]) -> None:
        name = vector["name"]
        nid = _counter_id()

        deadline = vector.get("deadline")
        state = RunState(
            run_id=vector["input"][0].get("runId", f"v_{name}"),
            deadline=deadline,
        )
        policy = _resolve_policy(vector)

        # Phase 1: feed all input events (with inline approval resolution
        # when the policy gates an event).
        self._feed_input_events(state, vector["input"], nid, policy)

        # Phase 2: special post-processing.
        if name == "timeout":
            # Deadline tick with now well past the deadline.
            step(state, None, now=TIMEOUT_TICK, new_id=nid)

        # Phase 3: finalize → wire result.
        result = finalize(state)
        result_record = result_to_record(result)

        # Assert schema-valid.
        errors = _validate_against_schema(result_record, _RESULT_SCHEMA)
        assert not errors, (
            f"{name}: Result schema invalid: {'; '.join(errors)}"
        )

        # Assert structural match.
        expected = vector["expected"]
        _compare_result(result_record, expected, name)
