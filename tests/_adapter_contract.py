"""Adapter contract: invariants every Adapter must satisfy, checked against both
the built-in ``mock_adapter`` and the real ``acp_adapter`` (Client ↔ a loopback
agent). Mirrors the style of ``_session_store_contract.py``.

Any future adapter (proxy, controller, …) should be exercised by the same
``_assert_contract`` helper.
"""

from __future__ import annotations

import os
import sys

import pytest

from conduit_sdk import Client, Runner, acp_adapter, mock_adapter
from conduit_sdk.runlayer import Agent

HERE = os.path.dirname(os.path.abspath(__file__))
RICH_AGENT = [sys.executable, os.path.join(HERE, "_rich_agent_app.py")]


def _assert_contract(events: list) -> None:
    """Invariants holding for any well-formed adapter event stream."""
    assert events, "adapter yielded no events"
    types = [e.type for e in events]

    # 1) The first event is always run.started.
    assert types[0] == "run.started", f"first event must be run.started; got {types[0]!r}"

    # 2) Exactly one terminal event, and it is last.
    terminal = [t for t in types if t in ("run.completed", "run.failed", "run.cancelled")]
    assert len(terminal) == 1, f"expected exactly one terminal event; got {terminal}"
    assert types[-1] == terminal[0], (
        f"terminal event must be last; got {types[-1]!r} with {terminal}"
    )

    # 3) Sequence numbers are strictly increasing from 1.
    seqs = [e.sequence for e in events]
    assert seqs == list(range(1, len(events) + 1)), (
        f"sequences must be 1..N strictly increasing; got {seqs}"
    )

    # 4) If a tool.completed appears, its ok flag reflects a real status
    #    (a bool, not None/missing) — guards the terminal-ToolCallUpdate mapping.
    for e in events:
        if e.type == "tool.completed":
            assert isinstance((e.payload or {}).get("ok"), bool), (
                f"tool.completed must carry a boolean ok; got {e.payload}"
            )


@pytest.mark.asyncio
async def test_mock_adapter_satisfies_contract() -> None:
    adapter = mock_adapter([
        ("agent.message.delta", {"text": "hi"}),
        ("tool.started", {"toolName": "t"}),
        ("tool.completed", {"toolName": "t", "ok": True}),
    ])
    run = await Runner.start(Agent(name="m"), task="x", adapter=adapter)
    events = [e async for e in run.events()]
    _assert_contract(events)


@pytest.mark.asyncio
async def test_acp_adapter_satisfies_contract() -> None:
    async with Client(RICH_AGENT, timeout=15) as client:
        adapter = acp_adapter(client)
        run = await Runner.start(Agent(name="a"), task="read the config", adapter=adapter)
        events = [e async for e in run.events()]
    _assert_contract(events)
    # The rich agent completes a tool, so ok must be True here.
    completed = [e for e in events if e.type == "tool.completed"]
    assert completed and completed[0].payload["ok"] is True
