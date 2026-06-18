"""Verification tests for the canonical SessionEvent model + unified pipeline.

Covers the NEW behaviours introduced by the canonical-event work that the
older suites did not exercise:

  * a *failing* tool surfaces as ``tool.completed`` with ``ok=False`` and a
    populated ``output`` (the legacy adapter always reported ``ok=True`` and
    dropped tool output);
  * ``prompt_sync`` (the batch path) fires lifecycle hooks AND persists to the
    session store — previously both were skipped outside ``prompt_stream``;
  * when ``AgentOptions.redaction_filter`` is set, secrets are scrubbed from
    BOTH the yielded ``SessionEvent`` AND the persisted store record.
"""

from __future__ import annotations

import os
import sys

import pytest

from conduit_sdk import (
    AgentOptions,
    Client,
    HookType,
    Runner,
    acp_adapter,
    redact_patterns,
)
from conduit_sdk.events import ToolCallUpdate
from conduit_sdk.runlayer import Agent
from conduit_sdk.session_store import InMemorySessionStore

HERE = os.path.dirname(os.path.abspath(__file__))
FAILING_AGENT = [sys.executable, os.path.join(HERE, "_failing_tool_agent_app.py")]
RICH_AGENT = [sys.executable, os.path.join(HERE, "_rich_agent_app.py")]


@pytest.mark.asyncio
async def test_failing_tool_surfaces_ok_false_with_output() -> None:
    """A failed tool call yields tool.completed with ok=False + output text."""
    async with Client(FAILING_AGENT, timeout=15) as client:
        adapter = acp_adapter(client)
        run = await Runner.start(
            Agent(name="t", instructions=""), task="go", adapter=adapter
        )
        events = [e async for e in run.events()]

    completed = [e for e in events if e.type == "tool.completed"]
    assert len(completed) == 1, f"expected one tool.completed; got {len(completed)}"
    payload = completed[0].payload or {}
    assert payload.get("ok") is False, f"expected ok=False; got {payload.get('ok')}"
    assert "boom" in (payload.get("outputPreview") or ""), (
        f"expected output text; got {payload.get('outputPreview')!r}"
    )


@pytest.mark.asyncio
async def test_batch_prompt_fires_hooks_and_persists() -> None:
    """prompt_sync (the batch path) fires hooks and persists to the store.

    Both were previously skipped outside prompt_stream; the unified pipeline
    routes every prompt method through the same _stream_events core.
    """
    store = InMemorySessionStore()
    options = AgentOptions(session_store=store)
    client = Client(RICH_AGENT, options=options, timeout=15)
    pre_tool_fired = []

    @client.hooks.on(HookType.PreToolUse)
    async def _on_pre(ctx):
        pre_tool_fired.append(ctx.get("tool_use_id"))

    async with client:
        await client.new_session()
        await client.prompt_sync("read config", session_id="s-batch")

    # Hook fired on the batch path.
    assert pre_tool_fired, "PreToolUse hook did not fire on the prompt_sync path"
    # Persistence happened on the batch path.
    records = await store.load_updates("s-batch")
    assert records, "session store is empty after prompt_sync (batch path skipped persist)"


@pytest.mark.asyncio
async def test_redaction_before_storage_and_yield() -> None:
    """A redaction_filter scrubs the secret from both the event and the record."""
    store = InMemorySessionStore()
    options = AgentOptions(
        session_store=store,
        redaction_filter=redact_patterns(r"SECRET-\w+"),
    )

    yielded_outputs: list[str] = []
    async with Client(FAILING_AGENT, options=options, timeout=15) as client:
        await client.new_session()
        async for event in client.prompt_stream("go", session_id="s-redact"):
            if isinstance(event, ToolCallUpdate) and event.output:
                yielded_outputs.append(event.output)

    # 1) The yielded SessionEvent has the secret scrubbed.
    assert yielded_outputs, "no ToolCallUpdate output was yielded"
    for out in yielded_outputs:
        assert "SECRET-abc123" not in out, (
            f"secret leaked into yielded event output: {out!r}"
        )
        assert "[REDACTED]" in out, f"expected redaction placeholder; got {out!r}"

    # 2) The persisted store record has the secret scrubbed.
    records = await store.load_updates("s-redact")
    blob = repr(records)
    assert "SECRET-abc123" not in blob, "secret leaked into persisted store record"
