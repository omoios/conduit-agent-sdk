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

import asyncio
import dataclasses
import functools
from collections.abc import Callable
from typing import Any

from conduit_sdk._conduit_sdk import HookType
from conduit_sdk.types import HookContext

__all__ = ["HookType", "HookRunner", "RegisteredHook", "hook"]


@dataclasses.dataclass
class RegisteredHook:
    """A registered hook with its metadata.

    Attributes
    ----------
    hook_type:
        The lifecycle event this hook listens for.
    callback:
        The async callable to invoke.
    priority:
        Execution order (lower = earlier). Default 0.
    matcher:
        Optional predicate. If set, the hook only runs when
        ``matcher(context)`` returns ``True``.
    timeout:
        Optional timeout in seconds. If the callback takes longer,
        it is skipped and dispatch continues.
    blocking:
        If ``True`` and the hook returns a blocked context (via
        ``"block"`` sentinel or ``ctx.get("blocked")`` truthy),
        dispatch stops immediately and further hooks of that type
        are not run.
    """
    hook_type: HookType
    callback: Callable
    priority: int = 0
    matcher: Callable[[HookContext], bool] | None = None
    timeout: float | None = None
    blocking: bool = False


class HookRunner:
    """Manages lifecycle hooks for a client connection.

    Provides decorator-based registration and dispatches hooks
    directly in Python for simplicity and correct callback invocation.
    """

    def __init__(self) -> None:
        self._hooks: list[RegisteredHook] = []

    def on(
        self,
        hook_type: HookType,
        *,
        priority: int = 0,
        matcher: Callable[[HookContext], bool] | None = None,
        timeout: float | None = None,
        blocking: bool = False,
    ) -> Callable:
        """Decorator to register a hook callback.

        Parameters
        ----------
        hook_type:
            The lifecycle event to hook into.
        priority:
            Execution order (lower = earlier). Default 0.
        matcher:
            Optional predicate. If set, the hook only runs when
            ``matcher(context)`` returns ``True``.
        timeout:
            Optional timeout in seconds. If the callback takes longer,
            it is skipped and dispatch continues.
        blocking:
            If ``True`` and the hook returns a blocked context,
            dispatch stops immediately.

        The decorated function should accept a :class:`HookContext` and
        return a (possibly modified) :class:`HookContext`, or ``None``
        to pass through unchanged.
        """

        def decorator(fn: Callable) -> Callable:
            self._hooks.append(RegisteredHook(
                hook_type=hook_type,
                callback=fn,
                priority=priority,
                matcher=matcher,
                timeout=timeout,
                blocking=blocking,
            ))

            @functools.wraps(fn)
            async def wrapper(ctx: HookContext) -> HookContext | None:
                return await fn(ctx)

            return wrapper

        return decorator

    async def dispatch(self, hook_type: HookType, context: HookContext) -> HookContext:
        """Dispatch hooks of the given type with the provided context.

        Calls matching hooks in priority order (lower = earlier).
        Hooks are filtered by ``hook_type`` and then by ``matcher``.
        If a hook has a ``timeout``, it is run with ``asyncio.wait_for``;
        a timeout results in the hook being skipped.
        If a hook has ``blocking=True`` and its result signals a block
        (the callback returns the sentinel ``"block"`` or
        ``context.get("blocked")`` is truthy), dispatch stops early
        and ``context.set("blocked", True)`` is set.

        Returns the (possibly modified) context after all hooks run.
        """
        # Filter by hook_type and matcher, sort by priority.
        matching = []
        for rh in self._hooks:
            if rh.hook_type != hook_type:
                continue
            if rh.matcher is not None:
                try:
                    if not rh.matcher(context):
                        continue
                except Exception:
                    continue
            matching.append(rh)
        matching.sort(key=lambda rh: rh.priority)

        for rh in matching:
            try:
                if rh.timeout is not None:
                    result = await asyncio.wait_for(
                        rh.callback(context), timeout=rh.timeout
                    )
                else:
                    result = await rh.callback(context)
            except asyncio.TimeoutError:
                continue
            except Exception:
                continue

            if result is not None:
                # Check blocking sentinel *before* assigning result to context.
                if rh.blocking:
                    if result == "block":
                        context.set("blocked", True)
                        break
                    if isinstance(result, HookContext) and result.get("blocked", False):
                        context = result
                        break
                context = result
            elif rh.blocking and context.get("blocked", False):
                # Hook returned None but already set blocked on context
                break

        return context

    def clear(self, hook_type: HookType | None = None) -> None:
        """Remove hooks, optionally filtered by type."""
        if hook_type is not None:
            self._hooks = [
                rh for rh in self._hooks if rh.hook_type != hook_type
            ]
        else:
            self._hooks.clear()


def hook(
    hook_type: HookType,
    *,
    priority: int = 0,
    matcher: Callable[[HookContext], bool] | None = None,
    timeout: float | None = None,
    blocking: bool = False,
) -> Callable:
    """Standalone decorator for defining hooks outside a client context.

    These hooks must be manually registered with a :class:`HookRunner`
    later using ``runner._hooks.append(...)``.
    """

    def decorator(fn: Callable) -> Callable:
        fn._hook_type = hook_type  # type: ignore[attr-defined]
        fn._hook_priority = priority  # type: ignore[attr-defined]
        fn._hook_matcher = matcher  # type: ignore[attr-defined]
        fn._hook_timeout = timeout  # type: ignore[attr-defined]
        fn._hook_blocking = blocking  # type: ignore[attr-defined]

        @functools.wraps(fn)
        async def wrapper(ctx: HookContext) -> HookContext | None:
            return await fn(ctx)

        wrapper._hook_type = hook_type  # type: ignore[attr-defined]
        wrapper._hook_priority = priority  # type: ignore[attr-defined]
        wrapper._hook_matcher = matcher  # type: ignore[attr-defined]
        wrapper._hook_timeout = timeout  # type: ignore[attr-defined]
        wrapper._hook_blocking = blocking  # type: ignore[attr-defined]
        return wrapper

    return decorator
