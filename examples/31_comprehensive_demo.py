# /// script
# requires-python = ">=3.12"
# dependencies = ["conduit-agent-sdk"]
# ///
"""31 — Comprehensive feature demo.

A guided tour of the SDK over the real ACP wire.

* Part 1 drives a bundled deterministic loopback agent (examples/_demo_agent.py)
  so every feature is shown instantly and reliably:
    - lifecycle hooks (Connected / SessionCreated / PromptSubmit / PreToolUse /
      PostToolUse / Stop) firing live
    - streaming deltas (thoughts + text)
    - tool-output observability (observe_turn)
    - the normalized Run layer (Runner + acp_adapter -> AgentEvent stream + Result)
    - elicitation round-trip (agent asks the client for input)
    - session persistence + replay (SessionStore via AgentOptions)
    - session create / delete
* Part 2 runs the same client API against a live ``omp acp`` agent if ``omp``
  is on PATH (introspection, streaming, multi-turn memory) — skipped otherwise.

    uv run examples/31_comprehensive_demo.py
"""

import asyncio
import os
import shutil
import sys

from conduit_sdk import (
    AgentOptions,
    Client,
    ElicitationResponse,
    HookType,
    Runner,
    UpdateKind,
    acp_adapter,
)
from conduit_sdk.runlayer import Agent
from conduit_sdk.session_store import InMemorySessionStore
from conduit_sdk.toolview import observe_turn

HERE = os.path.dirname(os.path.abspath(__file__))
DEMO_AGENT = [sys.executable, os.path.join(HERE, "_demo_agent.py")]


def banner(title: str) -> None:
    print(f"\n{'=' * 66}\n  {title}\n{'=' * 66}", flush=True)


def register_hooks(client: Client) -> None:
    """Print every lifecycle hook as it fires."""

    @client.hooks.on(HookType.Connected)
    async def _connected(ctx):
        print("   [hook] Connected", flush=True)

    @client.hooks.on(HookType.SessionCreated)
    async def _session(ctx):
        print(f"   [hook] SessionCreated  {(ctx.get('session_id') or '')[:12]}", flush=True)

    @client.hooks.on(HookType.PromptSubmit)
    async def _prompt(ctx):
        print(f"   [hook] PromptSubmit    {ctx.get('text')!r}", flush=True)

    @client.hooks.on(HookType.PreToolUse)
    async def _pre(ctx):
        print(f"   [hook] PreToolUse      {ctx.get('tool_name')}", flush=True)

    @client.hooks.on(HookType.PostToolUse)
    async def _post(ctx):
        print(f"   [hook] PostToolUse     {ctx.get('tool_use_id')} ({ctx.get('tool_status')})", flush=True)

    @client.hooks.on(HookType.Stop)
    async def _stop(ctx):
        print(f"   [hook] Stop            {ctx.get('stop_reason')}", flush=True)


async def _elicit(req) -> ElicitationResponse:
    """Answer the agent's elicitation request (would be a UI prompt in real life)."""
    print(f"   [elicit] agent asks: {req.message!r}", flush=True)
    return ElicitationResponse(action="accept", content={"name": "Ada Lovelace"})


async def loopback_tour() -> None:
    store = InMemorySessionStore()
    client = Client(
        DEMO_AGENT,
        options=AgentOptions(session_store=store, elicitation_handler=_elicit),
        timeout=15,
    )
    register_hooks(client)

    async with client:
        banner("1 . Streaming — live deltas from the agent")
        sid = (await client.new_session()).session_id
        async for u in client.prompt_stream("read config", session_id=sid):
            if u.kind == UpdateKind.ThoughtDelta:
                print(f"   thought: {u.text}", flush=True)
            elif u.kind == UpdateKind.TextDelta:
                print(f"   text:    {u.text}", flush=True)

        banner("2 . Tool-output observability — observe_turn")
        turn = await observe_turn(client, "read config", session_id=sid)
        print(f"   final text: {turn.text!r}", flush=True)
        for tc in turn.tool_calls:
            print(f"   tool: {tc.name:<20} input={tc.input}", flush=True)
            print(f"         output={tc.output!r}", flush=True)

        banner("3 . Run layer — normalized AgentEvent stream")
        run = await Runner.start(
            Agent(name="demo", instructions=""),
            task="read config",
            adapter=acp_adapter(client),
        )
        async for ev in run.events():
            print(f"   #{ev.sequence:<2} {ev.type:<22} source={ev.source}", flush=True)
        res = await run.result()
        print(f"   -> result: status={res.status} events={res.event_count}", flush=True)

        banner("4 . Elicitation — agent requests input from the client")
        out = await client.prompt_sync("please fill out the form", session_id=sid)
        print(f"   agent said: {''.join(m.text() for m in out)!r}", flush=True)

        banner("5 . Session persistence — replay from the store")
        for s in await store.list_sessions():
            recs = await store.load_updates(s)
            print(f"   session {s[:12]}: {len(recs)} updates persisted", flush=True)

        banner("6 . Session management — create & delete")
        s2 = await client.new_session()
        print(f"   + created {s2.session_id[:12]}", flush=True)
        deleted = await client.delete_session(s2.session_id)
        print(f"   - deleted {s2.session_id[:12]}  (agent returned {deleted})", flush=True)


async def live_omp_tour() -> None:
    omp = shutil.which("omp")
    if not omp:
        banner("Part 2 . Live omp acp — SKIPPED (omp not on PATH)")
        return

    banner("Part 2 . Live omp acp agent")
    client = Client([omp, "acp"], timeout=60)
    register_hooks(client)
    async with client:
        info = await client.agent_info
        if info:
            print(f"   agent: {info.get('name')} v{info.get('version')}", flush=True)

        sid = (await client.new_session()).session_id
        print("   streaming a one-liner:", flush=True)
        print("   ", end="", flush=True)
        async for u in client.prompt_stream(
            "In one short sentence, what is the Agent Client Protocol?",
            session_id=sid,
        ):
            if u.kind == UpdateKind.TextDelta and u.text:
                print(u.text, end="", flush=True)
        print(flush=True)

        await client.prompt_sync("Remember the secret word: ORANGE.", session_id=sid)
        out = await client.prompt_sync(
            "What is the secret word? Reply with just the word.", session_id=sid
        )
        print(f"   multi-turn recall: {''.join(m.text() for m in out).strip()!r}", flush=True)


async def main() -> int:
    print("conduit-agent-sdk - comprehensive feature demo", flush=True)
    banner("Part 1 . Deterministic loopback agent")
    await loopback_tour()
    await live_omp_tour()
    print("\nDemo complete.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
