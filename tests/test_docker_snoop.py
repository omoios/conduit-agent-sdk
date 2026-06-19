"""Deterministic snoop test: in-container SDK inspects a read-only mounted codebase.

A real ``conduit_sdk`` ``AgentServer`` runs INSIDE the ``conduit-sandbox`` image
and reports structured metadata about a read-only mounted workspace (file tree,
marker files, byte counts). No model, no credentials, no network — fully
hardened, CI-safe once the image is built.

The test proves the real SDK AgentServer is running inside the container,
reading from the host bind-mount, and producing a deterministic JSON report.

Build the image first (one-time, ~minutes to compile the Rust ext):
    infra/sandbox/build.sh conduit-sandbox:dev

Skipped automatically when the image is absent.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess

import pytest

from conduit_sdk import Runner
from conduit_sdk.docker_adapter import sandbox_agent_spec
from conduit_sdk.runlayer import Agent, AgentEvent, Result, acp_agent

from tests._adapter_contract import _assert_contract

IMAGE = os.environ.get("CONDUIT_SANDBOX_IMAGE", "conduit-sandbox:dev")

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "snoop_target")


def _image_present() -> bool:
    if shutil.which("docker") is None:
        return False
    return (
        subprocess.run(
            ["docker", "image", "inspect", IMAGE],
            capture_output=True,
        ).returncode
        == 0
    )


pytestmark = pytest.mark.skipif(
    not _image_present(),
    reason=f"build the sandbox image first: infra/sandbox/build.sh {IMAGE}",
)


@pytest.mark.asyncio
async def test_snoop_mounted_codebase() -> None:
    """Snoop agent inside the container inspects a read-only mounted fixture
    codebase and reports structured findings over docker-stdio."""
    spec = sandbox_agent_spec(
        IMAGE,
        workspace=FIX,
        command=["/opt/snoop_agent.py"],
    )
    run = await Runner.start(
        Agent(name="sandbox-snoop"),
        task="snoop the workspace",
        adapter=acp_agent(spec, timeout=120),
    )
    events: list[AgentEvent] = [e async for e in run.events()]
    result: Result = await run.result()

    _assert_contract(events)
    assert result.status == "completed", f"got {result.status}"
    out = result.final_output or ""
    assert "SANDBOX SNOOP REPORT" in out, f"missing report header in {out[:200]}"

    report = json.loads(out.split("\n", 1)[1])
    assert report["environment"]["work_mounted"] is True
    assert report["workspace"]["markers"]["README.md"] is True
    assert report["workspace"]["markers"]["pyproject.toml"] is True
    assert "src/" in report["workspace"]["top_level"]
    assert report["workspace"]["file_count"] >= 3
