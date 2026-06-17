"""Tests for conduit_sdk.hooks (HookRunner, RegisteredHook, and decorators)."""

from __future__ import annotations

import asyncio

import pytest

from conduit_sdk import HookRunner, HookType, hook
from conduit_sdk.hooks import RegisteredHook
from conduit_sdk.types import HookContext


class TestHookRunner:
    def test_init(self):
        runner = HookRunner()
        assert runner._hooks == []

    def test_on_decorator_collects_hooks(self):
        runner = HookRunner()

        @runner.on(HookType.PreToolUse)
        async def my_hook(ctx: HookContext) -> HookContext:
            return ctx

        assert len(runner._hooks) == 1
        rh = runner._hooks[0]
        assert isinstance(rh, RegisteredHook)
        assert rh.hook_type == HookType.PreToolUse
        assert rh.priority == 0
        assert rh.matcher is None
        assert rh.timeout is None
        assert rh.blocking is False

    def test_priority_ordering(self):
        runner = HookRunner()

        @runner.on(HookType.PromptSubmit, priority=10)
        async def late_hook(ctx: HookContext) -> HookContext:
            return ctx

        @runner.on(HookType.PromptSubmit, priority=1)
        async def early_hook(ctx: HookContext) -> HookContext:
            return ctx

        # Both registered.
        assert len(runner._hooks) == 2

    def test_clear_all(self):
        runner = HookRunner()

        @runner.on(HookType.Connected)
        async def h(ctx: HookContext) -> HookContext:
            return ctx

        runner.clear()
        assert runner._hooks == []

    def test_clear_by_type(self):
        runner = HookRunner()

        @runner.on(HookType.Connected)
        async def h1(ctx: HookContext) -> HookContext:
            return ctx

        @runner.on(HookType.Disconnected)
        async def h2(ctx: HookContext) -> HookContext:
            return ctx

        runner.clear(HookType.Connected)
        assert len(runner._hooks) == 1
        assert runner._hooks[0].hook_type == HookType.Disconnected


class TestStandaloneHookDecorator:
    def test_hook_decorator_sets_attributes(self):
        @hook(HookType.PostToolUse, priority=5)
        async def my_hook(ctx: HookContext) -> HookContext:
            return ctx

        assert my_hook._hook_type == HookType.PostToolUse
        assert my_hook._hook_priority == 5
        assert my_hook._hook_matcher is None
        assert my_hook._hook_timeout is None
        assert my_hook._hook_blocking is False

    def test_hook_decorator_with_all_params(self):
        def matcher(ctx: HookContext) -> bool:
            return ctx.get("x") == 1

        @hook(HookType.PreToolUse, priority=3, matcher=matcher, timeout=5.0, blocking=True)
        async def my_hook(ctx: HookContext) -> HookContext:
            return ctx

        assert my_hook._hook_type == HookType.PreToolUse
        assert my_hook._hook_priority == 3
        assert my_hook._hook_matcher is matcher
        assert my_hook._hook_timeout == 5.0
        assert my_hook._hook_blocking is True


class TestHookContext:
    def test_get_set(self):
        ctx = HookContext(hook_type="test", data={"key": "value"})
        assert ctx.get("key") == "value"
        assert ctx.get("missing", "default") == "default"

        ctx.set("new_key", 42)
        assert ctx.get("new_key") == 42


class TestDispatch:
    """Integration tests for runner.dispatch()."""

    @pytest.mark.asyncio
    async def test_basic_dispatch(self):
        runner = HookRunner()
        collector = []

        @runner.on(HookType.PreToolUse)
        async def hook_a(ctx: HookContext) -> HookContext:
            collector.append("a")
            return ctx

        @runner.on(HookType.PreToolUse, priority=10)
        async def hook_b(ctx: HookContext) -> HookContext:
            collector.append("b")
            return ctx

        ctx = HookContext(hook_type="test", data={})
        result = await runner.dispatch(HookType.PreToolUse, ctx)
        assert collector == ["a", "b"]
        assert result is ctx

    @pytest.mark.asyncio
    async def test_matcher_filters_out_unmatched(self):
        runner = HookRunner()
        ran = []

        def match_true(ctx: HookContext) -> bool:
            return True

        def match_false(ctx: HookContext) -> bool:
            return False

        @runner.on(HookType.PreToolUse, matcher=match_true)
        async def should_run(ctx: HookContext) -> HookContext:
            ran.append("yes")
            return ctx

        @runner.on(HookType.PreToolUse, matcher=match_false)
        async def should_not_run(ctx: HookContext) -> HookContext:
            ran.append("no")
            return ctx

        ctx = HookContext(hook_type="test", data={})
        await runner.dispatch(HookType.PreToolUse, ctx)
        assert ran == ["yes"]

    @pytest.mark.asyncio
    async def test_timeout_skips_slow_hook(self):
        runner = HookRunner()
        ran = []

        @runner.on(HookType.PreToolUse, timeout=0.05)
        async def slow_hook(ctx: HookContext) -> HookContext:
            await asyncio.sleep(10)  # much longer than timeout
            ran.append("slow")
            return ctx

        @runner.on(HookType.PreToolUse, priority=10)
        async def fast_hook(ctx: HookContext) -> HookContext:
            ran.append("fast")
            return ctx

        ctx = HookContext(hook_type="test", data={})
        # Should complete within ~0.1s (not 10s).
        await runner.dispatch(HookType.PreToolUse, ctx)
        # slow_hook did NOT run (timed out), fast_hook DID.
        assert ran == ["fast"]

    @pytest.mark.asyncio
    async def test_blocking_stops_dispatch(self):
        runner = HookRunner()
        ran = []

        @runner.on(HookType.PreToolUse, priority=1, blocking=True)
        async def blocker(ctx: HookContext) -> HookContext:
            ran.append("blocker")
            ctx.set("blocked", True)
            return ctx

        @runner.on(HookType.PreToolUse, priority=10)
        async def later_hook(ctx: HookContext) -> HookContext:
            ran.append("later")
            return ctx

        ctx = HookContext(hook_type="test", data={})
        result = await runner.dispatch(HookType.PreToolUse, ctx)
        assert ran == ["blocker"]  # later_hook did NOT run
        assert result.get("blocked") is True

    @pytest.mark.asyncio
    async def test_blocking_sentinel_block(self):
        runner = HookRunner()
        ran = []

        @runner.on(HookType.PreToolUse, priority=1, blocking=True)
        async def blocker(ctx: HookContext) -> HookContext:
            ran.append("blocker")
            return "block"  # sentinel string

        @runner.on(HookType.PreToolUse, priority=10)
        async def later_hook(ctx: HookContext) -> HookContext:
            ran.append("later")
            return ctx

        ctx = HookContext(hook_type="test", data={})
        result = await runner.dispatch(HookType.PreToolUse, ctx)
        assert ran == ["blocker"]
        assert result.get("blocked") is True

    @pytest.mark.asyncio
    async def test_non_blocking_hook_does_not_stop_dispatch(self):
        runner = HookRunner()
        ran = []

        @runner.on(HookType.PreToolUse, priority=1, blocking=False)
        async def non_blocker(ctx: HookContext) -> HookContext:
            ran.append("first")
            ctx.set("blocked", True)
            return ctx

        @runner.on(HookType.PreToolUse, priority=10)
        async def later_hook(ctx: HookContext) -> HookContext:
            ran.append("later")
            return ctx

        ctx = HookContext(hook_type="test", data={})
        result = await runner.dispatch(HookType.PreToolUse, ctx)
        assert ran == ["first", "later"]  # both run because blocking=False
        assert result.get("blocked") is True  # context still has blocked flag


class TestNewEventTypes:
    """Verify that all new HookType variants are importable and usable."""

    NEW_TYPES = [
        HookType.UserPromptSubmit,
        HookType.Stop,
        HookType.SubagentStart,
        HookType.SubagentStop,
        HookType.PermissionRequest,
        HookType.Notification,
        HookType.PreCompact,
    ]

    def test_new_types_are_importable(self):
        for ht in self.NEW_TYPES:
            assert ht is not None

    @pytest.mark.asyncio
    async def test_new_types_work_with_on_decorator(self):
        runner = HookRunner()
        for ht in self.NEW_TYPES:
            @runner.on(ht)
            async def dummy(ctx: HookContext) -> HookContext:
                return ctx

        assert len(runner._hooks) == len(self.NEW_TYPES)
        for rh in runner._hooks:
            assert rh.hook_type in self.NEW_TYPES
