"""Wire-contract tests for the Background Agent SDK canonical schemas + records.

Phase 0.5: every conformance vector's input events are schema-valid and round-
trip through runcore.to_record/from_record; every expected Result is valid
against schema/result.schema.json. These vectors are reused verbatim by the
Rust (Stage 2) and TS (Stage 3) ports, so a drift here is a cross-language
contract break.

The reducer-driven conformance (from_record -> step -> finalize -> expected)
lands in Phase 12 (tests/test_conformance.py); this file covers only the wire
shape + record round-trip.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conduit_sdk.runcore import (
    AgentEvent,
    Result,
    from_record,
    result_from_record,
    result_to_record,
    to_record,
)
from conduit_sdk.tools import _validate_against_schema

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_DIR = REPO_ROOT / "schema"
VECTOR_DIR = Path(__file__).resolve().parent / "vectors"


def _load_schema(name: str) -> dict:
    with (SCHEMA_DIR / name).open() as fh:
        return json.load(fh)


def _load_vectors() -> list[dict]:
    files = sorted(VECTOR_DIR.glob("*.json"))
    assert files, f"no conformance vectors found under {VECTOR_DIR}"
    out = []
    for f in files:
        with f.open() as fh:
            data = json.load(fh)
        data["__path"] = f.name
        out.append(data)
    return out


_EVENT_SCHEMA = _load_schema("agent_event.schema.json")
_RESULT_SCHEMA = _load_schema("result.schema.json")
_VECTORS = _load_vectors()


# ---------------------------------------------------------------------------
# AgentEvent record round-trip
# ---------------------------------------------------------------------------


class TestAgentEventRecord:
    """to_record/from_record bridge snake_case Python <-> camelCase wire."""

    def test_round_trip_preserves_all_fields(self) -> None:
        e = AgentEvent(
            id="abc",
            type="run.started",
            run_id="r1",
            sequence=1,
            timestamp="2026-01-01T00:00:00+00:00",
            source="sdk",
            redaction_status="none",
            summary="hi",
            payload={"text": "x"},
        )
        assert from_record(to_record(e)) == e

    def test_round_trip_with_null_optionals(self) -> None:
        e = AgentEvent(
            id="abc", type="run.completed", run_id="r1", sequence=2,
            timestamp="t", source="sdk", redaction_status="none",
            summary=None, payload=None,
        )
        assert from_record(to_record(e)) == e
    def test_to_record_omits_none_optionals(self) -> None:
        """Sparse wire convention: None-valued optional fields are omitted."""
        e = AgentEvent(
            id="i", type="run.started", run_id="r", sequence=1,
            timestamp="ts", source="sdk", redaction_status="none",
            summary=None, payload=None,
        )
        rec = to_record(e)
        assert "summary" not in rec
        assert "payload" not in rec
        # Required fields are always present.
        for k in ("id", "type", "runId", "sequence", "timestamp", "source", "redactionStatus"):
            assert k in rec
        rec = to_record(e)
        assert "runId" in rec and "run_id" not in rec
        assert "redactionStatus" in rec and "redaction_status" not in rec
        # version is now a real envelope field (P1); reserved tracing fields
        # are still omitted until set.
        assert rec.get("version") == 1
        assert "traceId" not in rec

    def test_from_record_ignores_unknown_wire_keys(self) -> None:
        """Newer producers adding fields must not break older consumers."""
        rec = {
            "id": "i", "type": "run.started", "runId": "r", "sequence": 1,
            "timestamp": "ts", "source": "sdk", "redactionStatus": "none",
            "futureField": "nope",
        }
        e = from_record(rec)
        assert e.id == "i" and e.run_id == "r" and e.redaction_status == "none"


# ---------------------------------------------------------------------------
# Result record round-trip
# ---------------------------------------------------------------------------


class TestResultRecord:
    """result_to_record/result_from_record bridge Python `error` <-> wire `failure`."""

    def test_round_trip_preserves_all_fields(self) -> None:
        r = Result(
            run_id="r1", status="failed", event_count=4,
            summary="boom", error={"code": "x", "message": "m", "retryable": True},
            started_at="s", ended_at="e",
        )
        assert result_from_record(result_to_record(r)) == r

    def test_error_serializes_as_wire_failure_key(self) -> None:
        r = Result(
            run_id="r", status="failed", event_count=1,
            error={"code": "agent_error", "message": "exploded"},
        )
        rec = result_to_record(r)
        assert "failure" in rec and "error" not in rec
        assert rec["failure"]["code"] == "agent_error"
        # And it deserializes back into the Python `error` field.
        assert result_from_record(rec).error == r.error


# ---------------------------------------------------------------------------
# Vector-driven schema validation
# ---------------------------------------------------------------------------


class TestVectors:
    """Every conformance vector: events schema-valid + round-trip; expected Result valid."""

    @pytest.fixture(params=_VECTORS, ids=[v["__path"] for v in _VECTORS])
    def vector(self, request) -> dict:
        return request.param

    def test_input_events_are_schema_valid(self, vector: dict) -> None:
        for i, rec in enumerate(vector["input"]):
            errors = _validate_against_schema(rec, _EVENT_SCHEMA, path=f"{vector['name']}.input[{i}]")
            assert not errors, f"{vector['name']} input[{i}] invalid: {errors}"

    def test_input_events_round_trip(self, vector: dict) -> None:
        for rec in vector["input"]:
            event = from_record(rec)
            # from_record(to_record(event)) == event (wire -> py -> wire -> py stable)
            assert from_record(to_record(event)) == event

    def test_expected_result_is_schema_valid(self, vector: dict) -> None:
        errors = _validate_against_schema(
            vector["expected"], _RESULT_SCHEMA, path=f"{vector['name']}.expected"
        )
        assert not errors, f"{vector['name']} expected Result invalid: {errors}"


# ---------------------------------------------------------------------------
# Catalog sanity (guards the SSOT list itself)
# ---------------------------------------------------------------------------


class TestEventCatalog:
    def test_catalog_loads_and_lists_terminal_types(self) -> None:
        catalog = json.loads((SCHEMA_DIR / "event_catalog.json").read_text())
        terminals = set(catalog["terminal_types"])
        assert terminals == {"run.completed", "run.failed", "run.cancelled", "run.timed_out"}
        statuses = set(catalog["statuses"])
        assert statuses == {"completed", "failed", "cancelled", "timed_out"}
        # Every catalog entry matches the envelope `type` pattern.
        import re
        pat = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")
        for group in catalog["events"].values():
            for name in group:
                assert pat.match(name), f"catalog name {name!r} fails type pattern"
