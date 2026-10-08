"""Phase 7 — Local buggy-calculator evidence proof.

A scripted-fixer adapter copies the buggy calculator fixtures into a temp
directory, fixes the bug (``a - b`` → ``a + b``), runs the test in-process,
and yields normalized events. The test asserts the ``Result`` carries the
expected evidence.
"""

from __future__ import annotations

import importlib.util
import difflib

import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from conduit_sdk.runlayer import Agent, AgentEvent, Result, Runner
from tests._adapter_contract import _assert_contract

HERE = Path(os.path.dirname(os.path.abspath(__file__)))
FIXTURE_DIR = HERE / "fixtures" / "buggy_calculator"


# ---------------------------------------------------------------------------
# Scripted-fixer adapter
# ---------------------------------------------------------------------------


class _BuggyCalcFixer:
    """Scripted adapter that fixes ``calc.py`` in-process, runs the test,
    and yields normalized events with evidence payloads."""

    name = "fixer"

    async def run(self, task: str, *, run_id: str):
        tmpdir = Path(tempfile.mkdtemp(prefix="buggy_calc_"))
        ts = datetime.now(timezone.utc).isoformat()
        try:
            # Copy fixtures into the temp workspace.
            src_calc = FIXTURE_DIR / "calc.py"
            src_test = FIXTURE_DIR / "test_calc.py"
            tmp_calc = tmpdir / "calc.py"
            tmp_test = tmpdir / "test_calc.py"
            shutil.copy2(str(src_calc), str(tmp_calc))
            shutil.copy2(str(src_test), str(tmp_test))

            # --- lifecycle: started ---
            yield AgentEvent(
                id="ev1", type="run.started", run_id=run_id,
                sequence=0, timestamp=ts, source="adapter",
                redaction_status="none",
            )

            # --- file.read ---
            yield AgentEvent(
                id="ev2", type="file.read", run_id=run_id,
                sequence=0, timestamp=ts, source="adapter",
                redaction_status="none",
                payload={"path": "calc.py"},
            )

            # Read original, fix the bug, write back.
            original = tmp_calc.read_text()
            fixed = original.replace("a - b", "a + b")

            # Compute unified diff.
            diff_lines = list(difflib.unified_diff(
                original.splitlines(keepends=True),
                fixed.splitlines(keepends=True),
                fromfile="calc.py", tofile="calc.py",
            ))
            diff_text = "".join(diff_lines)

            tmp_calc.write_text(fixed)

            # --- diff.preview_created ---
            yield AgentEvent(
                id="ev3", type="diff.preview_created", run_id=run_id,
                sequence=0, timestamp=ts, source="adapter",
                redaction_status="none",
                payload={"files": ["calc.py"], "diff": diff_text},
            )

            # --- diff.applied ---
            yield AgentEvent(
                id="ev4", type="diff.applied", run_id=run_id,
                sequence=0, timestamp=ts, source="adapter",
                redaction_status="none",
                payload={"files": ["calc.py"], "diff": diff_text},
            )

            # --- Run the fixture test in-process ---
            t0 = time.monotonic()

            # Import the fixed calc module.
            calc_spec = importlib.util.spec_from_file_location(
                "calc", str(tmp_calc),
            )
            calc_mod = importlib.util.module_from_spec(calc_spec)
            sys.modules["calc"] = calc_mod
            calc_spec.loader.exec_module(calc_mod)

            # Import and run test_calc (depends on calc being in sys.modules).
            test_spec = importlib.util.spec_from_file_location(
                "test_calc", str(tmp_test),
            )
            test_mod = importlib.util.module_from_spec(test_spec)
            sys.modules["test_calc"] = test_mod
            test_spec.loader.exec_module(test_mod)

            passed = True
            try:
                test_mod.test_add()
            except AssertionError:
                passed = False
            finally:
                sys.modules.pop("calc", None)
                sys.modules.pop("test_calc", None)

            duration_ms = int((time.monotonic() - t0) * 1000)
            msg = "passed" if passed else "FAILED"

            # --- test.started ---
            yield AgentEvent(
                id="ev5", type="test.started", run_id=run_id,
                sequence=0, timestamp=ts, source="adapter",
                redaction_status="none",
                payload={"command": "python test_calc.py"},
            )

            # --- test.output ---
            yield AgentEvent(
                id="ev6", type="test.output", run_id=run_id,
                sequence=0, timestamp=ts, source="adapter",
                redaction_status="none",
                payload={"text": f"test_add {msg}"},
            )

            # --- test.completed ---
            yield AgentEvent(
                id="ev7", type="test.completed", run_id=run_id,
                sequence=0, timestamp=ts, source="adapter",
                redaction_status="none",
                payload={
                    "command": "python test_calc.py",
                    "exitCode": 0 if passed else 1,
                    "passed": passed,
                    "durationMs": duration_ms,
                    "output": f"test_add {msg}",
                },
            )

            # --- agent.message.delta (final channel) ---
            yield AgentEvent(
                id="ev8", type="agent.message.delta", run_id=run_id,
                sequence=0, timestamp=ts, source="adapter",
                redaction_status="none",
                payload={"text": "Done", "channel": "final"},
            )

            # --- run.completed ---
            yield AgentEvent(
                id="ev9", type="run.completed", run_id=run_id,
                sequence=0, timestamp=ts, source="adapter",
                redaction_status="none",
                summary="Done",
            )

        finally:
            shutil.rmtree(str(tmpdir), ignore_errors=True)


# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_buggy_calculator_evidence() -> None:
    """Scripted fixer adapter → evidence-rich Result."""
    adapter = _BuggyCalcFixer()
    run = await Runner.start(
        Agent(name="fixer"),
        task="fix the calculator",
        adapter=adapter,
    )
    events: list[AgentEvent] = [e async for e in run.events()]
    result: Result = await run.result()

    # Contract invariants.
    _assert_contract(events)

    # Evidence assertions.
    assert result.status == "completed", f"expected completed, got {result.status}"
    assert result.changed_files == ["calc.py"], (
        f"expected ['calc.py'], got {result.changed_files}"
    )
    assert result.tests, "expected at least one test in result.tests"
    assert result.tests[0]["passed"] is True, (
        f"expected test passed=True, got {result.tests[0]}"
    )
    assert result.diff is not None, "expected diff to be present"
    assert "+    return a + b" in result.diff, (
        f"expected '+    return a + b' in diff, got:\n{result.diff}"
    )
    assert "Done" in (result.final_output or ""), (
        f"expected 'Done' in final_output, got {result.final_output!r}"
    )
