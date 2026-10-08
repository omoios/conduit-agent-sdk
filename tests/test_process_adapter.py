"""Tests for process_adapter (Phase 6)."""

from __future__ import annotations

import os
import sys

import pytest

from conduit_sdk.runlayer import Agent, Runner, process_adapter
from tests._adapter_contract import _assert_contract

HERE = os.path.dirname(os.path.abspath(__file__))
FAKE_APP = [sys.executable, os.path.join(HERE, "_fake_process_app.py")]


@pytest.mark.asyncio
async def test_process_adapter_happy_path() -> None:
    run = await Runner.start(
        Agent(name="p"), task="run", adapter=process_adapter(FAKE_APP),
    )
    events = [e async for e in run.events()]
    _assert_contract(events)
    types = [e.type for e in events]
    assert types[0] == "run.started"
    # The NDJSON tool + message events flow through.
    assert "agent.message.delta" in types
    assert "tool.completed" in types
    # The explicit run.completed from the script is the terminal.
    assert types[-1] == "run.completed"
    result = await run.result()
    assert result.status == "completed"


@pytest.mark.asyncio
async def test_process_adapter_non_json_lines_ignored() -> None:
    run = await Runner.start(
        Agent(name="p"), task="run", adapter=process_adapter(FAKE_APP),
    )
    events = [e async for e in run.events()]
    # Every event has a valid dotted type (no garbage leaked through).
    for e in events:
        assert "." in e.type


@pytest.mark.asyncio
async def test_process_adapter_failure_synthesizes_run_failed() -> None:
    run = await Runner.start(
        Agent(name="p"), task="run",
        adapter=process_adapter(FAKE_APP, env={**os.environ, "FAKE_PROCESS_FAIL": "1"}),
    )
    events = [e async for e in run.events()]
    _assert_contract(events)
    assert events[-1].type == "run.failed"
    result = await run.result()
    assert result.status == "failed"
    assert result.error is not None
    assert result.error["code"] == "process_failed"
    assert "2" in result.error["message"]  # exit code 2


@pytest.mark.asyncio
async def test_process_adapter_in_adapter_contract_suite() -> None:
    """The process adapter also satisfies the shared contract helper."""
    run = await Runner.start(
        Agent(name="p"), task="run", adapter=process_adapter(FAKE_APP),
    )
    events = [e async for e in run.events()]
    _assert_contract(events)
    # Sequences are strictly 1..N.
    assert [e.sequence for e in events] == list(range(1, len(events) + 1))
