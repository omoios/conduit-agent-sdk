"""In-container "agent" for the v1 Docker buggy-calculator proof.

Runs INSIDE the sandbox (cwd == the mounted workspace, ``/work``). It fixes
``calc.py`` (``a - b`` -> ``a + b``), runs the test in-process (no pytest
dependency in the image), and emits newline-delimited JSON ``AgentEvent``
records on stdout for ``process_adapter`` to parse.

It does NOT emit ``run.started`` / ``run.completed`` -- ``process_adapter``
synthesizes those from the process lifecycle. It always exits 0 (the *fix*
succeeded; the test outcome is carried in the ``test.completed`` payload), so
the host run terminates as ``run.completed``. Payloads mirror the in-process
fixer in ``tests/test_runlayer_fixture.py`` so the resulting Result evidence is
identical -- the seed's with/without-sandbox invariant.
"""

from __future__ import annotations

import difflib
import importlib.util
import json
import sys
import time


def emit(type_: str, payload: dict | None = None) -> None:
    rec: dict = {"type": type_}
    if payload is not None:
        rec["payload"] = payload
    sys.stdout.write(json.dumps(rec) + "\n")
    sys.stdout.flush()


def main() -> int:
    emit("file.read", {"path": "calc.py"})

    original = open("calc.py").read()
    fixed = original.replace("a - b", "a + b")
    diff_text = "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            fixed.splitlines(keepends=True),
            fromfile="calc.py",
            tofile="calc.py",
        )
    )
    with open("calc.py", "w") as fh:
        fh.write(fixed)

    # consumer keys: diff.preview_created -> Result.diff via payload["diff"] / files
    emit("diff.preview_created", {"files": ["calc.py"], "diff": diff_text})
    emit("diff.applied", {"files": ["calc.py"], "diff": diff_text})

    # Run the test in-process (mirrors the in-process fixer; no pytest needed).
    t0 = time.monotonic()
    calc_spec = importlib.util.spec_from_file_location("calc", "calc.py")
    calc_mod = importlib.util.module_from_spec(calc_spec)
    sys.modules["calc"] = calc_mod
    calc_spec.loader.exec_module(calc_mod)

    test_spec = importlib.util.spec_from_file_location("test_calc", "test_calc.py")
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

    emit("test.started", {"command": "python test_calc.py"})
    # consumer keys: test.completed -> Result.tests via command/exitCode/passed/durationMs/output
    emit(
        "test.completed",
        {
            "command": "python test_calc.py",
            "exitCode": 0 if passed else 1,
            "passed": passed,
            "durationMs": duration_ms,
            "output": f"test_add {msg}",
        },
    )
    # final-channel delta -> Result.final_output
    emit("agent.message.delta", {"text": "Done", "channel": "final"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
