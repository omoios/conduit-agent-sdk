"""Tests for ACP integration: acp_agent + the permission bridge (Phase 8).

Two layers:
  1. acp_agent launches a real loopback ACP agent (tests/_rich_agent_app.py)
     and surfaces tool calls as evidence — no live model.
  2. The permission bridge (Run._resolve_permission) routes a tool permission
     through the Policy/approval machinery, deadlock-free. The bridge needs a
     running consumer, so these use an idle adapter + a consumer task.
"""

from __future__ import annotations

import asyncio
import os
import sys

import pytest

from conduit_sdk.permissions import PermissionResultAllow, PermissionResultDeny
from conduit_sdk.policy import (
    Action,
    ApprovalDecision,
    ApprovalRequest,
    read_only,
    require_approval_for,
)
from conduit_sdk.runlayer import Agent, AgentEvent, Runner, acp_agent

HERE = os.path.dirname(os.path.abspath(__file__))
RICH_AGENT = [sys.executable, os.path.join(HERE, "_rich_agent_app.py")]
FAILING_AGENT = [sys.executable, os.path.join(HERE, "_failing_tool_agent_app.py")]


class _IdleAdapter:
    """Yields run.started then blocks forever — keeps the consumer alive."""

    name = "idle"

    async def run(self, task, *, run_id):
        yield AgentEvent(id="i", type="run.started", run_id=run_id, sequence=0,
                         timestamp="t", source="sdk", redaction_status="none")
        await asyncio.Event().wait()  # never completes


async def _drain(run) -> None:
    async for _ in run.events():
        pass


# ---------------------------------------------------------------------------
# acp_agent launch + evidence over the loopback agent
# ---------------------------------------------------------------------------


class TestAcpAgentLaunch:
    @pytest.mark.asyncio
    async def test_launch_streams_tools_and_completes(self) -> None:
        run = await Runner.start(
            Agent(name="acp"), task="read the config",
            adapter=acp_agent(RICH_AGENT, timeout=30),
        )
        events = [e async for e in run.events()]
        from tests._adapter_contract import _assert_contract
        _assert_contract(events)
        types = [e.type for e in events]
        # The rich agent's tool is kind="read" → surfaces as file.read (semantic)
        # plus the universal tool.completed lifecycle event.
        assert "file.read" in types and "tool.completed" in types
        result = await run.result()
        assert result.status == "completed"
        assert result.final_output and "8080" in result.final_output

    @pytest.mark.asyncio
    async def test_launch_failing_tool_agent(self) -> None:
        run = await Runner.start(
            Agent(name="acp"), task="use the bad tool",
            adapter=acp_agent(FAILING_AGENT, timeout=30),
        )
        events = [e async for e in run.events()]
        from tests._adapter_contract import _assert_contract
        _assert_contract(events)
        completed = [e for e in events if e.type == "tool.completed"]
        assert completed and completed[-1].payload["ok"] is False


# ---------------------------------------------------------------------------
# Permission bridge — driven directly via Run._resolve_permission
# ---------------------------------------------------------------------------


class TestPermissionBridge:
    @pytest.mark.asyncio
    async def test_allow(self) -> None:
        run = await Runner.start(Agent(name="x"), task="t",
                                 adapter=_IdleAdapter(), policy=read_only())
        consumer = asyncio.create_task(_drain(run))
        await asyncio.sleep(0)
        result = await run._resolve_permission(Action("tool", tool_name="read_file"))
        assert isinstance(result, PermissionResultAllow)
        consumer.cancel()
        try:
            await consumer
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_deny_emits_policy_blocked(self) -> None:
        run = await Runner.start(Agent(name="x"), task="t",
                                 adapter=_IdleAdapter(), policy=read_only())
        consumer = asyncio.create_task(_drain(run))
        await asyncio.sleep(0)
        result = await run._resolve_permission(Action("command", command="rm -rf /"))
        assert isinstance(result, PermissionResultDeny)
        assert any(e.type == "policy.blocked" for e in run._buffer)
        consumer.cancel()
        try:
            await consumer
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_require_approval_via_on_approval(self) -> None:
        seen: list[ApprovalRequest] = []

        def on_approval(req: ApprovalRequest) -> ApprovalDecision:
            seen.append(req)
            return ApprovalDecision(decision="approve", reason="ok")

        run = await Runner.start(
            Agent(name="x"), task="t", adapter=_IdleAdapter(),
            policy=require_approval_for(commands=["*"]), on_approval=on_approval,
        )
        consumer = asyncio.create_task(_drain(run))
        await asyncio.sleep(0)
        result = await run._resolve_permission(Action("command", command="git push"))
        assert isinstance(result, PermissionResultAllow)
        assert seen
        types = [e.type for e in run._buffer]
        assert "approval.required" in types and "approval.approved" in types
        consumer.cancel()
        try:
            await consumer
        except asyncio.CancelledError:
            pass

    @pytest.mark.asyncio
    async def test_external_approve_no_deadlock(self) -> None:
        """The bridge's external-approval path does not deadlock."""
        run = await Runner.start(
            Agent(name="x"), task="t", adapter=_IdleAdapter(),
            policy=require_approval_for(commands=["*"]),
        )
        consumer = asyncio.create_task(_drain(run))
        await asyncio.sleep(0)

        async def call_bridge():
            return await run._resolve_permission(Action("command", command="git push"))

        bridge_task = asyncio.create_task(call_bridge())

        # Wait for the approval to become pending, then approve externally.
        for _ in range(200):
            if run._state.pending_approval is not None:
                await run.approve(run._state.pending_approval[0], approved_by="test")
                break
            await asyncio.sleep(0.005)
        else:
            consumer.cancel()
            raise AssertionError("no pending approval appeared")

        res = await asyncio.wait_for(bridge_task, timeout=5)
        assert isinstance(res, PermissionResultAllow)
        consumer.cancel()
        try:
            await consumer
        except asyncio.CancelledError:
            pass
