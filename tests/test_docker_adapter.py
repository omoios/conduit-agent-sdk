"""v1 Docker sandbox proof.

Runs the buggy-calculator fix INSIDE a Docker container via :func:`docker_adapter`
and asserts the resulting ``Result`` carries the same evidence as the in-process
run in ``test_runlayer_fixture.py`` -- the seed's with/without-sandbox
event-stability invariant (seed SOURCE.md \u00a714 "v1: Local Docker sandbox").

Skipped when the ``docker`` CLI is absent.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

from conduit_sdk.docker_adapter import docker_adapter
from conduit_sdk.runlayer import Agent, AgentEvent, Result, Runner
from tests._adapter_contract import _assert_contract

HERE = Path(__file__).resolve().parent
FIXTURE_DIR = HERE / "fixtures" / "buggy_calculator"
IMAGE = os.environ.get(
    "CONDUIT_DOCKER_TEST_IMAGE", "nikolaik/python-nodejs:python3.12-nodejs22"
)

pytestmark = pytest.mark.skipif(
    shutil.which("docker") is None, reason="docker CLI not available"
)


@pytest.mark.asyncio
async def test_docker_buggy_calculator_evidence() -> None:
    """Fix + test the calculator inside a container; Result evidence matches the
    in-process proof."""
    # Temp workspace UNDER THE REPO tree so Docker Desktop can bind-mount it; the
    # default mkdtemp location (/var/folders on macOS) is not shared with the VM.
    workdir = Path(tempfile.mkdtemp(prefix="docker_calc_", dir=str(HERE)))
    try:
        for name in ("calc.py", "test_calc.py", "_docker_fixer.py"):
            shutil.copy2(str(FIXTURE_DIR / name), str(workdir / name))

        adapter = docker_adapter(
            IMAGE,
            ["python", "_docker_fixer.py"],
            workspace=str(workdir),
            platform="linux/amd64",
        )
        run = await Runner.start(
            Agent(name="docker-fixer"),
            task="fix the calculator inside a container",
            adapter=adapter,
        )
        events: list[AgentEvent] = [e async for e in run.events()]
        result: Result = await run.result()

        # Same contract + evidence as the in-process fixture proof.
        _assert_contract(events)
        assert result.status == "completed", f"got {result.status}"
        assert result.changed_files == ["calc.py"], result.changed_files
        assert result.tests and result.tests[0]["passed"] is True, result.tests
        assert result.diff is not None and "+    return a + b" in result.diff, result.diff
        assert "Done" in (result.final_output or "")

        # The fix is real on the mounted workspace (proves the bind-mount round-trip).
        assert "a + b" in (workdir / "calc.py").read_text()
    finally:
        shutil.rmtree(str(workdir), ignore_errors=True)
