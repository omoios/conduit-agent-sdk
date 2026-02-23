"""Tests for conduit_sdk.hooks (HookRunner and decorators)."""

from __future__ import annotations

import pytest

from conduit_sdk import HookRunner, HookType, hook
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
        ht, cb, priority = runner._hooks[0]
        assert ht == HookType.PreToolUse
        assert priority == 0

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
        assert runner._hooks[0][0] == HookType.Disconnected


class TestStandaloneHookDecorator:
    def test_hook_decorator_sets_attributes(self):
        @hook(HookType.PostToolUse, priority=5)
        async def my_hook(ctx: HookContext) -> HookContext:
            return ctx

        assert my_hook._hook_type == HookType.PostToolUse
        assert my_hook._hook_priority == 5


class TestHookContext:
    def test_get_set(self):
        ctx = HookContext(hook_type="test", data={"key": "value"})
        assert ctx.get("key") == "value"
        assert ctx.get("missing", "default") == "default"

        ctx.set("new_key", 42)
        assert ctx.get("new_key") == 42
