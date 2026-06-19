"""Opt-in e2e: drive a REAL ``omp acp`` agent INSIDE a Docker container.

This is the v1 sandbox (``docker run``) running a *real* ACP agent instead of a
scripted NDJSON fixer: ``acp_agent`` launches ``docker run -i --rm ... oh-my-pi
acp`` and speaks ACP JSON-RPC over the container's stdio (``-i`` keeps stdin
open). The seed's "run adapter inside sandbox, stream events to host" with a real
agent.

Skipped unless ``CONDUIT_E2E_ACP_DOCKER=1`` AND the ``docker`` CLI is present.
A real agent needs model credentials + network, so ``--network`` stays ON here
(``hardening_args(network=True, read_only_root=False)`` keeps caps/limits but
lets the agent write config/cache); host model-provider keys are forwarded into
the container via ``-e``.

    CONDUIT_E2E_ACP_DOCKER=1 OPENAI_API_KEY=... uv run pytest tests/test_docker_acp_e2e.py -v
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

from conduit_sdk import Runner
from conduit_sdk.docker_adapter import hardening_args
from conduit_sdk.runlayer import Agent, AgentEvent, Result, acp_agent
from tests._adapter_contract import _assert_contract

HERE = Path(__file__).resolve().parent
IMAGE = os.environ.get("CONDUIT_DOCKER_ACP_IMAGE", "oh-my-pi:15.8.0")
_ENABLED = os.environ.get("CONDUIT_E2E_ACP_DOCKER") == "1"

# Host env vars forwarded into the container if set (model-provider credentials).
_CRED_ENV_VARS = (
    "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GROQ_API_KEY",
    "OPENROUTER_API_KEY", "XAI_API_KEY", "MISTRAL_API_KEY", "DEEPSEEK_API_KEY",
)

pytestmark = pytest.mark.skipif(
    not (_ENABLED and shutil.which("docker")),
    reason="set CONDUIT_E2E_ACP_DOCKER=1 (needs docker + image + model creds + network)",
)


def _docker_acp_spec(workspace: str) -> list[str]:
    """``docker run`` argv that starts ``omp acp`` in a hardened-but-functional
    container. Network is ON (real model); ``-e VAR`` forwards any set creds."""
    argv = ["docker", "run", "-i", "--rm"]
    argv += hardening_args(network=True, read_only_root=False)
    for var in _CRED_ENV_VARS:
        if os.environ.get(var):
            argv += ["-e", var]  # forward host value into the container
    argv += ["-v", f"{workspace}:/work", "-w", "/work", IMAGE, "acp"]
    return argv


@pytest.mark.asyncio
async def test_real_omp_acp_in_container() -> None:
    """A real ``omp acp`` agent runs inside the container and the SDK collects a
    completed Result over the docker-stdio ACP transport."""
    workdir = Path(tempfile.mkdtemp(prefix="docker_acp_", dir=str(HERE)))
    try:
        run = await Runner.start(
            Agent(name="docker-acp"),
            task="In one sentence, what is the Agent Client Protocol?",
            adapter=acp_agent(_docker_acp_spec(str(workdir)), timeout=180),
        )
        events: list[AgentEvent] = [e async for e in run.events()]
        result: Result = await run.result()

        _assert_contract(events)
        assert result.status == "completed", f"got {result.status}"
        assert (result.final_output or "").strip(), "expected non-empty final output"
    finally:
        shutil.rmtree(str(workdir), ignore_errors=True)
