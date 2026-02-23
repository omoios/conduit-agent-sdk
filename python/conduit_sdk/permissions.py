"""Permission types and built-in policies for conduit-agent-sdk.

Provides the ``PermissionResult`` hierarchy and ready-made policy
functions that can be passed as the ``can_use_tool`` callback in
``AgentOptions``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# ---------------------------------------------------------------------------
# Permission result types
# ---------------------------------------------------------------------------


class PermissionResult:
    """Base class for permission decisions."""


class PermissionResultAllow(PermissionResult):
    """Approve a tool use request."""

    def __repr__(self) -> str:
        return "PermissionResultAllow()"


class PermissionResultDeny(PermissionResult):
    """Deny a tool use request.

    Parameters
    ----------
    reason:
        Human-readable explanation for the denial.
    """

    def __init__(self, reason: str = "") -> None:
        self.reason = reason

    def __repr__(self) -> str:
        return f"PermissionResultDeny(reason={self.reason!r})"


# ---------------------------------------------------------------------------
# Tool permission context
# ---------------------------------------------------------------------------


@dataclass
class ToolPermissionContext:
    """Context provided to a ``can_use_tool`` callback.

    Attributes
    ----------
    tool_name:
        Name of the tool the agent wants to invoke.
    tool_input:
        JSON string of the tool's input parameters.
    tool_use_id:
        Unique identifier for this tool invocation.
    session_id:
        Session in which the tool use occurs.
    """

    tool_name: str
    tool_input: str
    tool_use_id: str | None = None
    session_id: str | None = None


# ---------------------------------------------------------------------------
# Built-in policy functions
# ---------------------------------------------------------------------------


async def allow_all(
    tool_name: str,
    tool_input: str,
    context: ToolPermissionContext,
) -> PermissionResult:
    """Policy that approves every tool use request."""
    return PermissionResultAllow()


async def deny_all(
    tool_name: str,
    tool_input: str,
    context: ToolPermissionContext,
) -> PermissionResult:
    """Policy that denies every tool use request."""
    return PermissionResultDeny("all tool use denied by policy")


async def console_approve(
    tool_name: str,
    tool_input: str,
    context: ToolPermissionContext,
) -> PermissionResult:
    """Policy that prompts the user in the terminal for each tool use.

    Displays the tool name and input, then asks for ``y/n`` confirmation.
    """
    print(f"\n--- Permission request ---")
    print(f"Tool:  {tool_name}")
    print(f"Input: {tool_input}")
    answer = input("Allow? [y/N] ").strip().lower()
    if answer in ("y", "yes"):
        return PermissionResultAllow()
    return PermissionResultDeny("denied by user")
