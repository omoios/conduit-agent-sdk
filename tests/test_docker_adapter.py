"""v1 Docker sandbox proof + hardening.

Runs the buggy-calculator fix INSIDE a Docker container via :func:`docker_adapter`
and asserts the resulting ``Result`` carries the same evidence as the in-process
run in ``test_runlayer_fixture.py`` -- the seed's with/without-sandbox
event-stability invariant (seed SOURCE.md \u00a714 "v1: Local Docker sandbox") -- and
that the same holds with the sandbox locked down via :func:`hardening_args`.

Docker-backed tests are skipped when the ``docker`` CLI is absent; the pure
``hardening_args`` flag test always runs.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

from conduit_sdk.docker_adapter import docker_adapter, hardening_args
from conduit_sdk.runlayer import Agent, AgentEvent, Result, Runner
from tests._adapter_contract import _assert_contract

HERE = Path(__file__).resolve().parent
FIXTURE_DIR = HERE / "fixtures" / "buggy_calculator"
IMAGE = os.environ.get(
    "CONDUIT_DOCKER_TEST_IMAGE", "nikolaik/python-nodejs:python3.12-nodejs22"
)

requires_docker = pytest.mark.skipif(
    shutil.which("docker") is None, reason="docker CLI not available"
)


def _make_workspace() -> Path:
    # Temp workspace UNDER THE REPO tree so Docker Desktop can bind-mount it; the
    # default mkdtemp location (/var/folders on macOS) is not shared with the VM.
    workdir = Path(tempfile.mkdtemp(prefix="docker_calc_", dir=str(HERE)))
    for name in ("calc.py", "test_calc.py", "_docker_fixer.py"):
        shutil.copy2(str(FIXTURE_DIR / name), str(workdir / name))
    return workdir


async def _assert_fixed(adapter) -> Result:
    run = await Runner.start(
        Agent(name="docker-fixer"),
        task="fix the calculator inside a container",
        adapter=adapter,
    )
    events: list[AgentEvent] = [e async for e in run.events()]
    result: Result = await run.result()
    _assert_contract(events)
    assert result.status == "completed", f"got {result.status}"
    assert result.changed_files == ["calc.py"], result.changed_files
    assert result.tests and result.tests[0]["passed"] is True, result.tests
    assert result.diff is not None and "+    return a + b" in result.diff, result.diff
    assert "Done" in (result.final_output or "")
    return result


@requires_docker
@pytest.mark.asyncio
async def test_docker_buggy_calculator_evidence() -> None:
    """Plain in-container run produces the in-process evidence."""
    workdir = _make_workspace()
    try:
        adapter = docker_adapter(
            IMAGE, ["python", "_docker_fixer.py"],
            workspace=str(workdir), platform="linux/amd64",
        )
        await _assert_fixed(adapter)
        assert "a + b" in (workdir / "calc.py").read_text()  # bind-mount round-trip
    finally:
        shutil.rmtree(str(workdir), ignore_errors=True)


@requires_docker
@pytest.mark.asyncio
async def test_docker_hardened_run_unbroken() -> None:
    """The same proof holds with the sandbox hardened (no network, read-only
    root, dropped caps, resource caps) -- hardening must not break the legit
    workspace workflow."""
    workdir = _make_workspace()
    try:
        adapter = docker_adapter(
            IMAGE, ["python", "_docker_fixer.py"],
            workspace=str(workdir), platform="linux/amd64",
            extra_run_args=hardening_args(),
        )
        await _assert_fixed(adapter)
    finally:
        shutil.rmtree(str(workdir), ignore_errors=True)


def test_hardening_args_flags() -> None:
    """hardening_args emits the expected lock-down flags (no docker needed)."""
    args = hardening_args()
    assert ["--network", "none"] == args[:2]
    assert "--read-only" in args
    assert "--tmpfs" in args and "/tmp" in args
    assert "--cap-drop" in args and "ALL" in args
    assert "--security-opt" in args and "no-new-privileges" in args
    assert "--memory" in args and "--pids-limit" in args
    # user is opt-in (off by default to avoid bind-mount perm surprises)
    assert "--user" not in args
    assert "--user" in hardening_args(user="1000:1000")
    # network can be re-enabled
    assert "--network" not in hardening_args(network=True)
