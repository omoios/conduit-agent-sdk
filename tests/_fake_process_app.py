"""Fake process app for the process_adapter tests.

Emits newline-delimited JSON events on stdout. When the env var
``FAKE_PROCESS_FAIL=1`` is set, it exits non-zero to exercise the
``run.failed`` synthesis path.
"""

from __future__ import annotations

import json
import os
import sys


def _emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main() -> int:
    _emit({"type": "agent.message.delta", "payload": {"text": "hello"}})
    _emit({"type": "tool.started", "payload": {"toolName": "echo"}})
    _emit({"type": "tool.completed",
           "payload": {"toolName": "echo", "ok": True}})
    # A non-JSON line that must be ignored by the adapter.
    sys.stdout.write("this is not json\n")
    sys.stdout.flush()
    if os.environ.get("FAKE_PROCESS_FAIL") == "1":
        # Emit a stderr line (ignored) and exit non-zero WITHOUT a terminal,
        # so the adapter synthesizes run.failed.
        sys.stderr.write("boom\n")
        return 2
    _emit({"type": "run.completed", "summary": "done"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
