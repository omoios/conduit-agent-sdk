"""Tests for conduit_sdk.autoinstall (fake-adapter unit tests — the deterministic gate).

The real omp e2e is env-gated (CONDUIT_E2E_ACP=1) and skipped by default; these
tests drive the orchestrator logic with fake adapters that simulate the planner
(writes a manifest) and executor (makes a target command pass on attempt N).
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

import pytest

from conduit_sdk.autoinstall import autoinstall
from conduit_sdk.runlayer import AgentEvent

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "fixtures", "autoinstall_target")


class _FakeAdapter:
    """Yields run.started→run.completed; calls `action(cwd)` in between."""

    name = "fake"

    def __init__(self, action):
        self._action = action

    async def run(self, task, *, run_id):
        yield AgentEvent(id="i", type="run.started", run_id=run_id, sequence=0,
                         timestamp="t", source="sdk", redaction_status="none")
        self._action()
        yield AgentEvent(id="i2", type="run.completed", run_id=run_id, sequence=0,
                         timestamp="t", source="sdk", redaction_status="none")


class _Factory:
    """Fake adapter factory: planner writes a manifest; executor 'fixes' on attempt N."""

    def __init__(self, manifest, fix_at=1):
        self.manifest = manifest
        self.fix_at = fix_at
        self.s2 = 0

    def __call__(self, spec, *, cwd, timeout, enable_elicitation, **_):
        if cwd.endswith("stage1"):
            def act(c=cwd):
                if self.manifest is not None:
                    with open(os.path.join(c, "setup-manifest.json"), "w") as fh:
                        json.dump(self.manifest, fh)
            return _FakeAdapter(act)
        self.s2 += 1
        n = self.s2

        def act(c=cwd, n=n):
            if n >= self.fix_at:
                # "Fix": create a marker the target command checks.
                with open(os.path.join(c, "ready"), "w") as fh:
                    fh.write("ok")
        return _FakeAdapter(act)


def _manifest(target_cmd, expected_contains=None):
    return {
        "projectName": "autoinstall-target",
        "stack": ["python"],
        "setupSteps": [{"kind": "install", "command": "pip install -e ."}],
        "targetCommands": [
            {"command": target_cmd, "expectedExit": 0,
             "expectedOutputContains": expected_contains},
        ],
        "expectedOutcome": "the app imports and prints its greeting",
    }


@pytest.mark.asyncio
async def test_happy_path_verified():
    # app.py exists in the fixture copy → target passes immediately.
    m = _manifest(f"{sys.executable} app.py", "hello from autoinstall-target")
    res = await autoinstall(
        FIXTURE, agent_spec=["fake"], adapter_factory=_Factory(m),
        workdir=tempfile.mkdtemp(), keep_workdir=False,
    )
    assert res.verified is True
    assert res.attempts == 1
    assert res.manifest == m
    assert res.error is None
    assert res.verify and res.verify[0].passed


@pytest.mark.asyncio
async def test_manifest_missing_unverified():
    res = await autoinstall(
        FIXTURE, agent_spec=["fake"], adapter_factory=_Factory(manifest=None),
        workdir=tempfile.mkdtemp(),
    )
    assert res.verified is False
    assert res.attempts == 0
    assert "setup-manifest.json" in (res.error or "")


@pytest.mark.asyncio
async def test_manifest_invalid_unverified():
    # Missing required fields → schema-invalid.
    res = await autoinstall(
        FIXTURE, agent_spec=["fake"],
        adapter_factory=_Factory(manifest={"projectName": "x"}),
        workdir=tempfile.mkdtemp(),
    )
    assert res.verified is False
    assert res.error and "invalid" in res.error


@pytest.mark.asyncio
async def test_retry_converges_on_attempt_2():
    # Target fails until the 'ready' marker exists; executor creates it on attempt 2.
    target = (
        f"{sys.executable} -c \"import os,sys; sys.exit(0 if os.path.exists('ready') else 1)\""
    )
    m = _manifest(target)
    res = await autoinstall(
        FIXTURE, agent_spec=["fake"], adapter_factory=_Factory(m, fix_at=2),
        max_attempts=3, workdir=tempfile.mkdtemp(),
    )
    assert res.verified is True
    assert res.attempts == 2


@pytest.mark.asyncio
async def test_retry_exhausts_unverified():
    target = (
        f"{sys.executable} -c \"import os,sys; sys.exit(0 if os.path.exists('ready') else 1)\""
    )
    m = _manifest(target)
    res = await autoinstall(
        FIXTURE, agent_spec=["fake"], adapter_factory=_Factory(m, fix_at=99),
        max_attempts=2, workdir=tempfile.mkdtemp(),
    )
    assert res.verified is False
    assert res.attempts == 2
    assert res.verify and not res.verify[0].passed


@pytest.mark.asyncio
async def test_keep_workdir_populates_stages():
    m = _manifest(f"{sys.executable} app.py", "hello from autoinstall-target")
    wd = tempfile.mkdtemp()
    res = await autoinstall(
        FIXTURE, agent_spec=["fake"], adapter_factory=_Factory(m),
        workdir=wd, keep_workdir=True,
    )
    assert res.workdir == wd
    # Both stage copies exist; stage1 holds the manifest.
    assert os.path.isdir(os.path.join(wd, "stage1"))
    assert os.path.isdir(os.path.join(wd, "stage2"))
    assert os.path.exists(os.path.join(wd, "stage1", "setup-manifest.json"))

@pytest.mark.asyncio
async def test_no_agent_returns_error(monkeypatch):
    import conduit_sdk.autoinstall as ai
    monkeypatch.setattr(ai, "_default_spec", lambda: None)
    res = await autoinstall(
        FIXTURE, agent_spec=None, adapter_factory=None, workdir=tempfile.mkdtemp(),
    )
    assert res.verified is False
    assert res.error and "no agent" in res.error


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.environ.get("CONDUIT_E2E_ACP") != "1" or not shutil.which("omp"),
    reason="set CONDUIT_E2E_ACP=1 with omp installed for the live autoinstall e2e",
)
async def test_live_omp_autoinstall():
    """Live e2e: omp plans + executes setup of the fixture repo."""
    res = await autoinstall(FIXTURE, timeout=900, max_attempts=3)
    assert res.manifest is not None  # planner produced a manifest
    # The fixture is trivial; the import target should pass within retries.
    assert res.attempts >= 1
