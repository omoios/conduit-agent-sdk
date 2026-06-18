"""Client teardown must not hang when an agent descendant holds the ACP pipe.

Regression test for the disconnect hang: the agent is spawned in its own
process group and killed as a group (with a bounded reap), so a grandchild that
inherited the stdout pipe is also terminated and teardown completes promptly.
Before the fix (kill only the direct child) the driver process hangs forever.
"""

import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
DRIVER = os.path.join(HERE, "_disconnect_driver.py")


def test_disconnect_does_not_hang_with_pipe_holding_grandchild():
    try:
        proc = subprocess.run(
            [sys.executable, DRIVER],
            timeout=25,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("Client teardown hung: driver did not exit within 25s")
    assert proc.returncode == 0, f"driver failed (rc={proc.returncode}): {proc.stderr[-800:]}"
    assert "clean exit" in proc.stdout
