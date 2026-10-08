"""Tests for the approval gate + budgets (Phase 4).

Covers: on_approval approve/reject (event order + result.approvals), read_only
→ policy.blocked, external approve() in a separate task (no deadlock), approval
timeout → approval.expired, and cost-budget breach → budget.exceeded + run.failed.
"""

from __future__ import annotations

import asyncio

import pytest

from conduit_sdk.policy import (
    ApprovalDecision,
    ApprovalRequest,
    compose,
    max_cost_usd,
    read_only,
    require_approval_for,
)
from conduit_sdk.runlayer import Agent, AgentEvent, Runner, mock_adapter


def _cmd(command: str) -> tuple[str, dict]:
    return ("command.started", {"command": command})


# ---------------------------------------------------------------------------
# on_approval (callback resolves on the consumer task)
# ---------------------------------------------------------------------------


class TestOnApproval:
    @pytest.mark.asyncio
    async def test_approve_via_callback_proceeds(self) -> None:
        seen: list[ApprovalRequest] = []

        def on_approval(req: ApprovalRequest) -> ApprovalDecision:
            seen.append(req)
            return ApprovalDecision(decision="approve", reason="ok", by="tester")

        run = await Runner.start(
            Agent(name="x"), task="t",
            adapter=mock_adapter([_cmd("git push origin")]),
            policy=require_approval_for(commands=["git push"]),
            on_approval=on_approval,
        )
        events = [e async for e in run.events()]
        types = [e.type for e in events]
        assert "approval.required" in types
        assert types.index("approval.required") < types.index("approval.approved")
        assert types.index("approval.approved") < types.index("command.started")
        result = await run.result()
        assert result.status == "completed"
        assert result.approvals and result.approvals[0]["decision"] == "approved"
        assert seen[0].kind == "command"

    @pytest.mark.asyncio
    async def test_reject_via_callback_blocks_action(self) -> None:
        run = await Runner.start(
            Agent(name="x"), task="t",
            adapter=mock_adapter([_cmd("rm -rf /")]),
            policy=require_approval_for(commands=["rm"]),
            on_approval=lambda req: ApprovalDecision(decision="reject", reason="nope"),
        )
        events = [e async for e in run.events()]
        types = [e.type for e in events]
        assert "approval.required" in types
        assert "approval.rejected" in types
        assert "policy.blocked" in types
        # The gated command never proceeds.
        assert "command.started" not in types
        result = await run.result()
        assert result.approvals[0]["decision"] == "rejected"

    @pytest.mark.asyncio
    async def test_async_on_approval_supported(self) -> None:
        async def on_approval(req: ApprovalRequest) -> ApprovalDecision:
            await asyncio.sleep(0)
            return ApprovalDecision(decision="approve")

        run = await Runner.start(
            Agent(name="x"), task="t",
            adapter=mock_adapter([_cmd("git push")]),
            policy=require_approval_for(commands=["git push"]),
            on_approval=on_approval,
        )
        result = await run.result()
        assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_on_approval_returning_non_decision_is_rejected(self) -> None:
        run = await Runner.start(
            Agent(name="x"), task="t",
            adapter=mock_adapter([_cmd("git push")]),
            policy=require_approval_for(commands=["git push"]),
            on_approval=lambda req: "not a decision",  # type: ignore[arg-type]
        )
        events = [e async for e in run.events()]
        assert any(e.type == "approval.rejected" for e in events)
        assert "command.started" not in [e.type for e in events]

    @pytest.mark.asyncio
    async def test_on_approval_raising_is_rejected(self) -> None:
        def on_approval(req: ApprovalRequest) -> ApprovalDecision:
            raise RuntimeError("approver unavailable")

        run = await Runner.start(
            Agent(name="x"), task="t",
            adapter=mock_adapter([_cmd("git push")]),
            policy=require_approval_for(commands=["git push"]),
            on_approval=on_approval,
        )
        events = [e async for e in run.events()]
        rejected = [e for e in events if e.type == "approval.rejected"]
        assert rejected and "approver unavailable" in (rejected[0].payload or {}).get("reason", "")


# ---------------------------------------------------------------------------
# read_only → policy.blocked
# ---------------------------------------------------------------------------


class TestReadOnlyDeny:
    @pytest.mark.asyncio
    async def test_deny_emits_policy_blocked(self) -> None:
        run = await Runner.start(
            Agent(name="x"), task="t",
            adapter=mock_adapter([_cmd("ls")]),
            policy=read_only(),
        )
        events = [e async for e in run.events()]
        types = [e.type for e in events]
        assert "policy.blocked" in types
        assert "command.started" not in types


# ---------------------------------------------------------------------------
# External approve()/reject() (no on_approval) — no deadlock
# ---------------------------------------------------------------------------


class TestExternalApproval:
    @pytest.mark.asyncio
    async def test_external_approve_in_separate_task(self) -> None:
        run = await Runner.start(
            Agent(name="x"), task="t",
            adapter=mock_adapter([_cmd("git push")]),
            policy=require_approval_for(commands=["git push"]),
        )
        events: list[AgentEvent] = []
        ready = asyncio.Event()

        async def consume() -> None:
            async for ev in run.events():
                if ev.type == "approval.required":
                    ready.set()
                events.append(ev)

        consumer = asyncio.create_task(consume())
        await ready.wait()
        # Find the pending approval id from the state.
        approval_id = run._state.pending_approval[0]  # type: ignore[attr-defined]
        await run.approve(approval_id, approved_by="kevin")
        await consumer
        types = [e.type for e in events]
        assert "approval.approved" in types
        assert "command.started" in types
        result = await run.result()
        assert result.status == "completed"

    @pytest.mark.asyncio
    async def test_external_reject(self) -> None:
        run = await Runner.start(
            Agent(name="x"), task="t",
            adapter=mock_adapter([_cmd("rm")]),
            policy=require_approval_for(commands=["rm"]),
        )
        events: list[AgentEvent] = []
        ready = asyncio.Event()

        async def consume() -> None:
            async for ev in run.events():
                if ev.type == "approval.required":
                    ready.set()
                events.append(ev)

        consumer = asyncio.create_task(consume())
        await ready.wait()
        approval_id = run._state.pending_approval[0]  # type: ignore[attr-defined]
        await run.reject(approval_id, reason="dangerous")
        await consumer
        types = [e.type for e in events]
        assert "approval.rejected" in types
        assert "policy.blocked" in types
        assert "command.started" not in types

    @pytest.mark.asyncio
    async def test_approve_unknown_id_raises(self) -> None:
        run = await Runner.start(
            Agent(name="x"), task="t", adapter=mock_adapter([]),
        )
        with pytest.raises(ValueError):
            await run.approve("nonexistent")


# ---------------------------------------------------------------------------
# Approval timeout → approval.expired
# ---------------------------------------------------------------------------


class TestApprovalTimeout:
    @pytest.mark.asyncio
    async def test_approval_expires(self) -> None:
        run = await Runner.start(
            Agent(name="x"), task="t",
            adapter=mock_adapter([_cmd("git push")]),
            policy=require_approval_for(commands=["git push"]),
            approval_timeout_s=0.05,
        )
        result = await run.result()
        events = run._buffer  # type: ignore[attr-defined]
        types = [e.type for e in events]
        assert "approval.expired" in types
        assert "policy.blocked" in types
        assert result.approvals[0]["decision"] == "expired"


# ---------------------------------------------------------------------------
# Cost budget breach → budget.exceeded + run.failed
# ---------------------------------------------------------------------------


class TestCostBudget:
    @pytest.mark.asyncio
    async def test_cost_breach_fails(self) -> None:
        run = await Runner.start(
            Agent(name="x"), task="t",
            adapter=mock_adapter([
                ("cost.updated", {"costUsd": 5.0}),
            ]),
            policy=compose(max_cost_usd(1.0)),
        )
        result = await run.result()
        events = run._buffer  # type: ignore[attr-defined]
        types = [e.type for e in events]
        assert "budget.exceeded" in types
        assert result.status == "failed"
        assert result.error is not None
        assert result.error["code"] == "budget_exceeded"
