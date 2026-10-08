# /// script
# requires-python = ">=3.12"
# dependencies = ["conduit-agent-sdk"]
# ///
"""32 — Buggy calculator fix: evidence proof.

Demonstrates the scripted-fixer adapter pattern: copies the buggy calculator
fixture, fixes ``a - b`` → ``a + b`` in-process, runs the test, and prints
the evidence-rich ``Result``.
"""

from __future__ import annotations

import difflib
import importlib.util
import os
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
import asyncio

from conduit_sdk.runlayer import Agent, AgentEvent, Result, Runner

HERE = Path(os.path.dirname(os.path.abspath(__file__)))
FIXTURE_DIR = HERE.parent / "tests" / "fixtures" / "buggy_calculator"


class _BuggyCalcFixer:
    """Scripted adapter that fixes ``calc.py`` and runs the test in-process."""

    name = "fixer"

    async def run(self, task: str, *, run_id: str):
        tmpdir = Path(tempfile.mkdtemp(prefix="buggy_calc_"))
        ts = datetime.now(timezone.utc).isoformat()
        try:
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

            # Import and run test_calc.
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


async def main() -> int:
    print("Buggy calculator fix — evidence proof", flush=True)
    print("─" * 50, flush=True)

    adapter = _BuggyCalcFixer()
    run = await Runner.start(
        Agent(name="fixer"),
        task="fix the calculator",
        adapter=adapter,
    )

    events: list[AgentEvent] = []
    async for ev in run.events():
        events.append(ev)
        # Print each event as a compact line.
        line = f"  [{ev.sequence:3d}] {ev.type}"
        if ev.type in ("diff.preview_created", "diff.applied"):
            files = (ev.payload or {}).get("files", [])
            line += f"  files={files}"
        elif ev.type == "test.completed":
            p = ev.payload or {}
            line += f"  passed={p.get('passed')}"
        elif ev.type == "agent.message.delta":
            text = (ev.payload or {}).get("text", "")
            line += f"  text={text!r}"
        print(line, flush=True)

    result: Result = await run.result()

    print(f"\nResult status: {result.status}", flush=True)
    print(f"Changed files: {result.changed_files}", flush=True)
    print(f"Tests: {result.tests}", flush=True)
    print(f"Final output: {result.final_output!r}", flush=True)
    print(f"\nDiff ({len(result.diff or '')} bytes):", flush=True)
    print(result.diff or "<no diff>", flush=True)

    # Verify evidence (same assertions as the test).
    assert result.status == "completed"
    assert result.changed_files == ["calc.py"]
    assert result.tests and result.tests[0]["passed"] is True
    assert result.diff is not None and "+    return a + b" in result.diff
    assert "Done" in (result.final_output or "")

    print("─" * 50, flush=True)
    print("All evidence assertions PASSED.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
