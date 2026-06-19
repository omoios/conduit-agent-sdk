"""Deterministic ACP-in-container proof (the real, CI-safe "B" path).

A real ``conduit_sdk`` ``AgentServer`` runs INSIDE the ``conduit-sandbox`` image
(built from a Linux wheel) and the host SDK drives it over ``docker run -i``
stdio via ``acp_agent``. No model, no credentials, no network -- so unlike the
gated ``omp acp`` e2e this runs fully hardened (``--network none``) and is safe
to run in CI once the image exists.

Build the image first (one-time, ~minutes to compile the Rust ext):
    infra/sandbox/build.sh conduit-sandbox:dev

Skipped automatically when the image is absent.
"""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from conduit_sdk import Runner
from conduit_sdk.docker_adapter import hardening_args
from conduit_sdk.runlayer import Agent, AgentEvent, Result, acp_agent

from tests._adapter_contract import _assert_contract

IMAGE = os.environ.get("CONDUIT_SANDBOX_IMAGE", "conduit-sandbox:dev")


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
async def test_real_agentserver_in_container() -> None:
    """The in-image AgentServer completes an ACP run over docker-stdio, fully
    network-isolated and capability-dropped."""
    # network none: the echo agent needs no network -> deterministic + hardened.
    spec = ["docker", "run", "-i", "--rm", *hardening_args(read_only_root=False), IMAGE]
    run = await Runner.start(
        Agent(name="sandbox-echo"),
        task="hello",
        adapter=acp_agent(spec, timeout=120),
    )
    events: list[AgentEvent] = [e async for e in run.events()]
    result: Result = await run.result()

    _assert_contract(events)
    assert result.status == "completed", f"got {result.status}"
    assert "echo from sandbox" in (result.final_output or ""), result.final_output
