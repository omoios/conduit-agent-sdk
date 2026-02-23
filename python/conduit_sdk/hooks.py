"""Lifecycle hook system for conduit-agent-sdk.

Hooks allow you to intercept and modify ACP protocol events at
specific points in the request/response lifecycle.

Example::

    runner = client.hooks

    @runner.on(HookType.PreToolUse)
    async def log_tool(ctx: HookContext) -> HookContext:
        print(f"Tool called: {ctx.get('tool_name')}")
        return ctx
"""

from __future__ import annotations

import functools
import json
from collections.abc import Callable
from typing import Any

from conduit_sdk._conduit_sdk import HookType, RustHookDispatcher
from conduit_sdk.types import HookContext

__all__ = ["HookType", "HookRunner", "hook"]


class HookRunner:
    """Manages lifecycle hooks for a client connection.

    Provides decorator-based registration and dispatches hooks through
    the Rust layer for performance.
    """

    def __init__(self) -> None:
        self._dispatcher = RustHookDispatcher()
        self._hooks: list[tuple[HookType, Callable, int]] = []

    def on(
        self,
        hook_type: HookType,
        *,
        priority: int = 0,
    ) -> Callable:
        """Decorator to register a hook callback.

        Parameters
        ----------
        hook_type:
            The lifecycle event to hook into.
        priority:
            Execution order (lower = earlier). Default 0.

        The decorated function should accept a :class:`HookContext` and
        return a (possibly modified) :class:`HookContext`, or ``None``
        to pass through unchanged.
        """

        def decorator(fn: Callable) -> Callable:
            self._hooks.append((hook_type, fn, priority))

            @functools.wraps(fn)
            async def wrapper(ctx: HookContext) -> HookContext | None:
                return await fn(ctx)

            return wrapper

        return decorator

    async def register_all(self) -> None:
        """Register all collected hooks with the Rust dispatcher."""
        for hook_type, callback, priority in self._hooks:
            await self._dispatcher.register(hook_type, callback, priority)

    async def dispatch(self, hook_type: HookType, context: HookContext) -> HookContext:
        """Dispatch hooks of the given type with the provided context.

        Returns the (possibly modified) context after all hooks run.
        """
        context_json = json.dumps({"hook_type": context.hook_type, "data": context.data})
        result_json = await self._dispatcher.dispatch(hook_type, context_json)
        result = json.loads(result_json)
        return HookContext(hook_type=result.get("hook_type", ""), data=result.get("data", {}))

    def clear(self, hook_type: HookType | None = None) -> None:
        """Remove hooks, optionally filtered by type."""
        if hook_type is not None:
            self._hooks = [(ht, cb, p) for ht, cb, p in self._hooks if ht != hook_type]
        else:
            self._hooks.clear()


def hook(
    hook_type: HookType,
    *,
    priority: int = 0,
) -> Callable:
    """Standalone decorator for defining hooks outside a client context.

    These hooks must be manually registered with a :class:`HookRunner`
    later using ``runner._hooks.append(...)``.
    """

    def decorator(fn: Callable) -> Callable:
        fn._hook_type = hook_type  # type: ignore[attr-defined]
        fn._hook_priority = priority  # type: ignore[attr-defined]

        @functools.wraps(fn)
        async def wrapper(ctx: HookContext) -> HookContext | None:
            return await fn(ctx)

        wrapper._hook_type = hook_type  # type: ignore[attr-defined]
        wrapper._hook_priority = priority  # type: ignore[attr-defined]
        return wrapper

    return decorator
