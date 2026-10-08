"""Tests for first-class elicitation (Slice 2).

Elicitation is a standalone agent→client request (NOT reducer-gated), surfaced
as elicitation.* events and resolved via on_elicitation, external
respond()/cancel_elicitation(), or timeout. Recorded into Result.questions.
"""

from __future__ import annotations

import asyncio

import pytest

from conduit_sdk.elicitation import ElicitationRequest, ElicitationResponse
from conduit_sdk.runlayer import Agent, AgentEvent, Runner


class _IdleAdapter:
    name = "idle"

    async def run(self, task, *, run_id):
        yield AgentEvent(id="i", type="run.started", run_id=run_id, sequence=0,
                         timestamp="t", source="sdk", redaction_status="none")
        await asyncio.Event().wait()


async def _drain(run) -> None:
    async for _ in run.events():
        pass


class TestElicitation:
    @pytest.mark.asyncio
    async def test_on_elicitation_responds(self) -> None:
        seen: list[ElicitationRequest] = []

        def on_elicitation(req: ElicitationRequest) -> ElicitationResponse:
            seen.append(req)
            return ElicitationResponse(action="accept", content={"name": "Alice"})

        run = await Runner.start(Agent(name="x"), task="t", adapter=_IdleAdapter(),
                                 on_elicitation=on_elicitation)
        consumer = asyncio.create_task(_drain(run))
        await asyncio.sleep(0)
        req = ElicitationRequest(message="What is your name?", mode="form")
        resp = await run._resolve_elicitation(req)
        assert resp.action == "accept"
        assert resp.content == {"name": "Alice"}
        assert seen
        types = [e.type for e in run._buffer]
        assert "elicitation.requested" in types and "elicitation.responded" in types
        consumer.cancel()
        try:
            await consumer
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_external_respond(self) -> None:
        run = await Runner.start(Agent(name="x"), task="t", adapter=_IdleAdapter())
        consumer = asyncio.create_task(_drain(run))
        await asyncio.sleep(0)

        async def ask():
            return await run._resolve_elicitation(ElicitationRequest(message="q"))

        task = asyncio.create_task(ask())
        # Wait for the elicitation to become pending, then answer externally.
        for _ in range(200):
            if run._pending_elicitations:
                eid = next(iter(run._pending_elicitations))
                await run.respond(eid, {"answer": 42})
                break
            await asyncio.sleep(0.005)
        else:
            raise AssertionError("no pending elicitation")
        resp = await asyncio.wait_for(task, timeout=5)
        assert resp.action == "accept"
        assert resp.content == {"answer": 42}
        consumer.cancel()
        try:
            await consumer
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_external_cancel(self) -> None:
        run = await Runner.start(Agent(name="x"), task="t", adapter=_IdleAdapter())
        consumer = asyncio.create_task(_drain(run))
        await asyncio.sleep(0)

        async def ask():
            return await run._resolve_elicitation(ElicitationRequest(message="q"))

        task = asyncio.create_task(ask())
        for _ in range(200):
            if run._pending_elicitations:
                eid = next(iter(run._pending_elicitations))
                await run.cancel_elicitation(eid)
                break
            await asyncio.sleep(0.005)
        resp = await asyncio.wait_for(task, timeout=5)
        assert resp.action == "cancel"
        types = [e.type for e in run._buffer]
        assert "elicitation.cancelled" in types
        consumer.cancel()
        try:
            await consumer
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_elicitation_timeout_expires(self) -> None:
        run = await Runner.start(Agent(name="x"), task="t", adapter=_IdleAdapter(),
                                 elicitation_timeout_s=0.05)
        consumer = asyncio.create_task(_drain(run))
        await asyncio.sleep(0)
        resp = await asyncio.wait_for(
            run._resolve_elicitation(ElicitationRequest(message="q")), timeout=5)
        assert resp.action == "cancel"
        assert "elicitation.expired" in [e.type for e in run._buffer]
        consumer.cancel()
        try:
            await consumer
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_questions_recorded_in_result(self) -> None:
        def on_elicitation(req):
            return ElicitationResponse(action="accept", content={"x": 1})

        run = await Runner.start(Agent(name="x"), task="t", adapter=_IdleAdapter(),
                                 on_elicitation=on_elicitation)
        consumer = asyncio.create_task(_drain(run))
        await asyncio.sleep(0)
        await run._resolve_elicitation(ElicitationRequest(message="how many?"))
        await run.cancel()
        await consumer
        result = await run.result()
        assert result.questions
        assert result.questions[0]["action"] in ("accept", "responded")
        assert result.questions[0]["content"] == {"x": 1}
