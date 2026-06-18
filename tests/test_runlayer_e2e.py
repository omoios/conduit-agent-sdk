"""Run-layer end-to-end tests using a real ACP loopback agent.

Spins up _rich_agent_app.py as a subprocess, wraps it with acp_adapter,
and exercises the full Run/Adapter lifecycle through Runner.

These tests are deterministic (no model) and fast.
"""

from __future__ import annotations

import os
import sys

import pytest

from conduit_sdk import Client, Runner, acp_adapter
from conduit_sdk.runlayer import Agent

HERE = os.path.dirname(os.path.abspath(__file__))
AGENT = [sys.executable, os.path.join(HERE, "_rich_agent_app.py")]


@pytest.mark.asyncio
async def test_acp_adapter_full_turn() -> None:
    """Drive the rich agent through acp_adapter and assert the full event stream.

    Covers: run.started, tool.started, tool.completed, agent.message.delta,
    run.completed, monotonically increasing sequence numbers, and the
    final Result object.
    """
    async with Client(AGENT, timeout=15) as client:
        adapter = acp_adapter(client)
        run = await Runner.start(
            Agent(name="t", instructions=""),
            task="read the config",
            adapter=adapter,
        )

        events: list = []
        async for event in run.events():
            events.append(event)

        # --- Lifecycle: first event must be run.started ---
        assert events[0].type == "run.started"
        assert events[0].source == "sdk"

        # --- Tool events ---
        tool_started = [e for e in events if e.type == "tool.started"]
        tool_completed = [e for e in events if e.type == "tool.completed"]
        assert len(tool_started) == 1
        # tool.completed is derived from the terminal ToolCallUpdate: it carries
        # the title (remembered from ToolCallStart), ok, callId, and the decoded
        # output — not the old id-only ToolUseEnd placeholder.
        assert tool_completed[0].payload["toolName"] == "Read config.txt"
        assert tool_completed[0].payload["ok"] is True
        assert tool_completed[0].payload["callId"] == "tc1"
        assert tool_completed[0].payload["outputPreview"] == "port = 8080"

        # --- Text deltas ---
        text_deltas = [e for e in events if e.type == "agent.message.delta"]
        assert len(text_deltas) >= 1
        assert any(
            "port 8080" in (e.payload or {}).get("text", "")
            for e in text_deltas
        )

        # --- Terminal: last event must be run.completed ---
        assert events[-1].type == "run.completed"

        # --- Sequence numbers: strictly increasing starting at 1 ---
        seqs = [e.sequence for e in events]
        assert seqs == list(range(1, len(events) + 1))

        # --- Result ---
        result = await run.result()
        assert result.status == "completed"
        assert result.event_count == len(events)
