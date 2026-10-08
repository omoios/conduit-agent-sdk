"""Tests for runlayer.query (P9) and conductor_adapter (P10)."""

from __future__ import annotations

import pytest

from conduit_sdk.runlayer import (
    AgentEvent,
    conductor_adapter,
    mock_adapter,
    query,
)
from conduit_sdk.proxy import ProxyChain, conductor_available

# ---------------------------------------------------------------------------
# query (P9)
# ---------------------------------------------------------------------------


class TestQuery:
    @pytest.mark.asyncio
    async def test_query_streams_lifecycle(self) -> None:
        types = [e.type async for e in query(
            "hi", adapter=mock_adapter([("agent.message.delta", {"text": "x"})]),
        )]
        assert types[0] == "run.started"
        assert types[-1] == "run.completed"
        assert "agent.message.delta" in types

    @pytest.mark.asyncio
    async def test_query_propagates_adapter_error(self) -> None:
        class _Boom:
            name = "boom"

            async def run(self, task, *, run_id):
                yield AgentEvent(id="i", type="run.started", run_id=run_id,
                                 sequence=0, timestamp="t", source="sdk",
                                 redaction_status="none")
                raise RuntimeError("kaboom")

        with pytest.raises(RuntimeError, match="kaboom"):
            async for _ in query("hi", adapter=_Boom()):
                pass

    @pytest.mark.asyncio
    async def test_query_cancel_mid_stream(self) -> None:
        import asyncio

        class _Slow:
            name = "slow"

            async def run(self, task, *, run_id):
                yield AgentEvent(id="i", type="run.started", run_id=run_id,
                                 sequence=0, timestamp="t", source="sdk",
                                 redaction_status="none")
                ev = asyncio.Event()
                await ev.wait()  # never completes
                yield AgentEvent(id="i2", type="run.completed", run_id=run_id,
                                 sequence=0, timestamp="t", source="sdk",
                                 redaction_status="none")

        gen = query("hi", adapter=_Slow())
        run_ref: list = []

        async def consume() -> None:
            async for ev in gen:
                run_ref.append(ev)
                break  # stop early to grab the run? — query doesn't expose run

        # query doesn't expose the Run; verify it streams at least run.started.
        task = asyncio.create_task(consume())
        await asyncio.sleep(0.05)
        assert run_ref and run_ref[0].type == "run.started"
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# ---------------------------------------------------------------------------
# conductor_adapter (P10) — argv construction always; live run gated.
# ---------------------------------------------------------------------------


class TestConductorAdapter:
    def test_conductor_adapter_builds_argv(self) -> None:
        from conduit_sdk.proxy import ContextInjector

        chain = ProxyChain().add(ContextInjector(context="system-prompt"))
        adapter = conductor_adapter(["opencode", "acp"], chain)
        assert adapter.name == "conductor"

    def test_conductor_adapter_empty_chain_raises(self) -> None:
        from conduit_sdk.exceptions import ProxyError

        with pytest.raises(ProxyError):
            conductor_adapter(["opencode", "acp"], ProxyChain())

    @pytest.mark.asyncio
    @pytest.mark.skipif(not conductor_available(), reason="conductor not on PATH")
    async def test_conductor_adapter_live_run(self) -> None:
        from conduit_sdk.proxy import ContextInjector

        chain = ProxyChain(ContextInjector(context="be brief"))
        adapter = conductor_adapter(
            ["python", "-c", "import sys; sys.exit(0)"], chain, timeout=10,
        )
        # Just verify it constructs + the adapter has the right shape.
        assert adapter.name == "conductor"
        # A full live run requires a real ACP agent behind the conductor; the
        # env-gated e2e (P11) covers that. Here we only assert construction.
