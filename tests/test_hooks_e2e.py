"""End-to-end: client lifecycle hooks fire during a real ACP turn.

Before wiring, `client.hooks.on(...)` registered callbacks that never ran. These
tests drive a real Client against the rich-agent fixture and assert the hooks
actually fire at the right lifecycle points — and that a blocking PromptSubmit
hook prevents the prompt from being sent.
"""

import os
import sys

import pytest

from conduit_sdk import Client, HookType
from conduit_sdk.exceptions import HookBlockedError

HERE = os.path.dirname(os.path.abspath(__file__))
RICH_AGENT = [sys.executable, os.path.join(HERE, "_rich_agent_app.py")]


@pytest.mark.asyncio
async def test_lifecycle_hooks_fire_during_a_turn():
    seen = {
        "connected": [],
        "session": [],
        "prompt": [],
        "pre_tool": [],
        "post_tool": [],
        "stop": [],
    }
    client = Client(RICH_AGENT, timeout=15)

    @client.hooks.on(HookType.Connected)
    async def _on_connected(ctx):
        seen["connected"].append(ctx.get("command"))

    @client.hooks.on(HookType.SessionCreated)
    async def _on_session(ctx):
        seen["session"].append(ctx.get("session_id"))

    @client.hooks.on(HookType.PromptSubmit)
    async def _on_prompt(ctx):
        seen["prompt"].append(ctx.get("text"))

    @client.hooks.on(HookType.PreToolUse)
    async def _on_pre(ctx):
        seen["pre_tool"].append(
            (ctx.get("tool_name"), ctx.get("tool_input"), ctx.get("tool_use_id"))
        )

    @client.hooks.on(HookType.PostToolUse)
    async def _on_post(ctx):
        seen["post_tool"].append(ctx.get("tool_use_id"))

    @client.hooks.on(HookType.Stop)
    async def _on_stop(ctx):
        seen["stop"].append(ctx.get("stop_reason"))

    async with client:
        async for _ in client.prompt_stream("read the config"):
            pass

    # Connection + (default) session lifecycle.
    assert seen["connected"], "Connected hook did not fire"
    assert seen["session"], "SessionCreated hook did not fire"
    # PromptSubmit saw the prompt text.
    assert seen["prompt"] == ["read the config"]
    # Tool lifecycle, with decoded context.
    assert seen["pre_tool"], "PreToolUse hook did not fire"
    name, tool_input, use_id = seen["pre_tool"][0]
    assert name == "Read config.txt"
    assert "config.txt" in (tool_input or "")
    assert use_id == "tc1"
    assert seen["post_tool"] == ["tc1"]
    # Stop fired with the turn's stop_reason.
    assert seen["stop"] and seen["stop"][0]


@pytest.mark.asyncio
async def test_blocking_promptsubmit_hook_prevents_send():
    client = Client(RICH_AGENT, timeout=15)

    @client.hooks.on(HookType.PromptSubmit, blocking=True)
    async def _block(ctx):
        return "block"

    async with client:
        await client.new_session()
        with pytest.raises(HookBlockedError):
            await client.prompt_sync("this must be blocked before sending")


@pytest.mark.asyncio
async def test_matcher_filters_hook():
    client = Client(RICH_AGENT, timeout=15)
    fired: list[str] = []

    # Only fire for tool calls whose name mentions "config".
    @client.hooks.on(
        HookType.PreToolUse,
        matcher=lambda ctx: "config" in (ctx.get("tool_name") or "").lower(),
    )
    async def _on_pre(ctx):
        fired.append(ctx.get("tool_name"))

    async with client:
        async for _ in client.prompt_stream("read the config"):
            pass

    assert fired == ["Read config.txt"]
