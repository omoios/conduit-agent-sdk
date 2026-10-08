"""End-to-end integration test: conduit_sdk run layer <-> real local ACP agent.

Opt-in only — skipped unless ``CONDUIT_E2E_ACP=1`` is set AND a local ACP
agent is available (``omp acp``, ``CONDUIT_E2E_ACP_CMD``, or
``CONDUIT_E2E_ACP_ID``). Exercises the full ``acp_agent`` adapter +
``Runner`` lifecycle against a live ACP server.

    CONDUIT_E2E_ACP=1 uv run pytest tests/test_runlayer_acp_e2e.py -v
"""

from __future__ import annotations

import os
import shlex
import shutil
import tempfile
from pathlib import Path

import pytest

from conduit_sdk import Runner
from conduit_sdk.policy import read_only
from conduit_sdk.runlayer import Agent, acp_agent
from tests._adapter_contract import _assert_contract


def _resolve_spec() -> list[str] | str | None:
    """Return an ``acp_agent``-compatible *spec*, or ``None`` if no agent is available.

    Priority:
    1. ``CONDUIT_E2E_ACP_CMD`` — shlex-split shell command
    2. ``CONDUIT_E2E_ACP_ID`` — raw registry id string
    3. ``["omp", "acp"]`` — if ``omp`` is on PATH
    """
    cmd = os.environ.get("CONDUIT_E2E_ACP_CMD")
    if cmd:
        return shlex.split(cmd)
    rid = os.environ.get("CONDUIT_E2E_ACP_ID")
    if rid:
        return rid
    if shutil.which("omp"):
        return ["omp", "acp"]
    return None


_SPEC = _resolve_spec()
_ENABLED = os.environ.get("CONDUIT_E2E_ACP") == "1"

pytestmark = pytest.mark.skipif(
    not (_SPEC and _ENABLED),
    reason=(
        "set CONDUIT_E2E_ACP=1 with a local ACP agent available"
        " (install `omp`, set CONDUIT_E2E_ACP_CMD, or set CONDUIT_E2E_ACP_ID)"
    ),
)


@pytest.mark.asyncio
async def test_streaming_and_result() -> None:
    """``Runner.run`` returns a completed ``Result`` with text; separate event stream satisfies contract."""
    spec = _SPEC  # guaranteed non-None by skipif

    # --- Via Runner.run convenience (single-shot) ---
    result = await Runner.run(
        Agent(name="e2e"),
        task="In one sentence, what is the Agent Client Protocol?",
        adapter=acp_agent(spec, timeout=120),
    )
    assert result.status == "completed", (
        f"expected completed, got {result.status}"
    )
    assert result.final_output and result.final_output.strip(), (
        f"expected non-empty final_output, got {result.final_output!r}"
    )

    # --- Via Runner.start + manual event iteration for contract ---
    run = await Runner.start(
        Agent(name="e2e"),
        task="In one sentence, what is the Agent Client Protocol?",
        adapter=acp_agent(spec, timeout=120),
    )
    events = [e async for e in run.events()]
    _assert_contract(events)
    seqs = [e.sequence for e in events]
    assert seqs == list(range(1, len(events) + 1)), (
        f"sequences must be 1..N strictly increasing; got {seqs}"
    )


@pytest.mark.asyncio
async def test_confirmation() -> None:
    """Coding task under ``read_only()`` policy completes; contract holds.

    Real agents vary in how they handle permission blocks — some negotiate
    with the user, others retry differently, and some silently continue after
    a denial. We only check that the run terminates (any terminal status) and
    that the event contract is valid.
    """
    spec = _SPEC
    run = await Runner.start(
        Agent(name="e2e"),
        task=(
            "Write a Python function that computes fibonacci numbers, "
            "then save it to /tmp/fib.py"
        ),
        adapter=acp_agent(spec, timeout=120),
        policy=read_only(),
    )
    events = [e async for e in run.events()]
    _assert_contract(events)

    result = await run.result()
    assert result.status in ("completed", "failed", "cancelled"), (
        f"expected completed/failed/cancelled, got {result.status}"
    )


@pytest.mark.asyncio
async def test_evidence() -> None:
    """File-creation task produces evidence (``changed_files`` or ``diff`` or ``tests``).

    Lenient: only requires at least one evidence field to be populated, since
    real agents may represent the edit differently. Cleans up the temp dir.
    """
    spec = _SPEC
    tmpdir = Path(tempfile.mkdtemp(prefix="acp_e2e_"))
    target = tmpdir / "hello.py"
    try:
        result = await Runner.run(
            Agent(name="e2e"),
            task=f"Create a file at {target} with a Python hello-world script",
            adapter=acp_agent(spec, timeout=120),
        )
        # At least one evidence field should be populated.
        has_evidence = bool(result.changed_files) or bool(result.diff) or bool(result.tests)
        assert has_evidence, (
            f"expected at least one evidence field populated; "
            f"changed_files={result.changed_files!r} "
            f"diff={result.diff!r} "
            f"tests={result.tests!r}"
        )
    finally:
        import shutil as _shutil

        _shutil.rmtree(str(tmpdir), ignore_errors=True)
