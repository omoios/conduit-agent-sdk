"""End-to-end test: session store persistence over the real ACP wire.

Drives a real ``Client`` against ``_rich_agent_app`` (which emits a thought,
a tool call+result, and a final text message) and verifies that every
streaming update is recorded into the ``InMemorySessionStore`` passed via
``AgentOptions(session_store=...)``.
"""

from __future__ import annotations

import os
import sys

import pytest

from conduit_sdk import AgentOptions, Client
from conduit_sdk.session_store import InMemorySessionStore

HERE = os.path.dirname(os.path.abspath(__file__))
AGENT = [sys.executable, os.path.join(HERE, "_rich_agent_app.py")]


@pytest.mark.asyncio
async def test_session_store_persists_rich_turn() -> None:
    """``InMemorySessionStore`` wired via ``AgentOptions`` persists every
    update a ``Client.prompt_stream`` yields for a rich agent turn.

    Asserts the store returns non-empty updates, that the recorded text
    includes the agent's final message, that a tool-call record is present,
    and that the session appears in ``list_sessions()``.
    """
    store = InMemorySessionStore()
    options = AgentOptions(session_store=store)

    async with Client(AGENT, options=options, timeout=15) as client:
        # Consume the full prompt_stream — the store persists inside the loop.
        async for _ in client.prompt_stream("read config", session_id="s-e2e"):
            pass

    # -- The store should now hold records for every streaming update ---------
    records = await store.load_updates("s-e2e")

    # 1) Non-empty
    assert len(records) > 0, "expected at least one persisted record"

    # 2) Agent text is persisted
    all_text = "".join(
        r.get("text", "") for r in records if isinstance(r.get("text"), str)
    )
    # The agent's final send_text produces a TextDelta with this string.
    assert "The config sets port 8080." in all_text, (
        f"agent text not found in persisted records; got: {all_text!r}"
    )

    # 3) The tool call is reflected as a ToolUseStart record.
    #    NOTE: the Rust client Debug-formats these ACP enum values, producing
    #    Rust-style capitalised variant names (e.g. "Read", "Pending") rather
    #    than the wire format ("read", "pending").
    tool_starts = [r for r in records if r.get("kind") == "UpdateKind.ToolUseStart"]
    assert len(tool_starts) == 1, (
        f"expected exactly one ToolUseStart record; got {len(tool_starts)}"
    )
    ts = tool_starts[0]
    assert ts.get("tool_name") == "Read config.txt"
    assert ts.get("tool_use_id") == "tc1"
    assert ts.get("tool_kind") == "Read"
    assert ts.get("tool_status") == "Pending"
    assert ts.get("tool_input") == '{"path":"config.txt"}'

    # 4) The tool completion is reflected as a ToolUseUpdate record
    tool_updates = [r for r in records if r.get("kind") == "UpdateKind.ToolUseUpdate"]
    assert len(tool_updates) == 1, (
        f"expected exactly one ToolUseUpdate record; got {len(tool_updates)}"
    )
    tu = tool_updates[0]
    assert tu.get("tool_use_id") == "tc1"
    assert tu.get("tool_status") == "Completed"

    # 5) A Done record with the correct stop_reason ends the turn.
    #    The Rust client Debug-formats the StopReason enum, so "end_turn"
    #    becomes "EndTurn".
    done_records = [r for r in records if r.get("kind") == "UpdateKind.Done"]
    assert len(done_records) == 1, (
        f"expected exactly one Done record; got {len(done_records)}"
    )
    assert done_records[0].get("stop_reason") == "EndTurn"

    # 6) The session is listed
    sessions = await store.list_sessions()
    assert "s-e2e" in sessions, (
        f"expected 's-e2e' in listed sessions; got {sorted(sessions)}"
    )
