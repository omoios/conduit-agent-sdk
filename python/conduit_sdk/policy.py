"""Structured policy for the run layer (core; ports to Rust).

A :class:`Policy` maps an :class:`Action` to a :class:`PolicyDecision`
(``allow`` / ``deny`` / ``require_approval``), optionally carrying a budget
(A5). Factory policies compose with **deny > require_approval > allow**
precedence. The same Policy decides both catalog gate events (A6) and live ACP
permission requests (P8), so confirmation behaves identically from a mock
script or a real agent.

Wire form is camelCase (A3); see ``schema/policy.schema.json``. Pure, sync, no
asyncio/IO — ports to Rust unchanged.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from typing import Any, Protocol

__all__ = [
    "Action",
    "PolicyDecision",
    "ApprovalRequest",
    "ApprovalDecision",
    "Policy",
    "read_only",
    "deny",
    "require_approval_for",
    "safe_local",
    "compose",
    "max_runtime_minutes",
    "max_cost_usd",
]


# ---------------------------------------------------------------------------
# Data types (camelCase wire via to_record; sparse — None omitted)
# ---------------------------------------------------------------------------


@dataclass
class Action:
    """A gateable action extracted from an event or permission request."""

    kind: str
    command: str | None = None
    path: str | None = None
    tool_name: str | None = None

    def to_record(self) -> dict[str, Any]:
        d: dict[str, Any] = {"kind": self.kind}
        if self.command is not None:
            d["command"] = self.command
        if self.path is not None:
            d["path"] = self.path
        if self.tool_name is not None:
            d["toolName"] = self.tool_name
        return d


@dataclass
class PolicyDecision:
    """The outcome of evaluating an Action: allow / deny / require_approval."""

    effect: str  # allow | deny | require_approval
    risk: str = "low"  # low | med | high
    reason: str | None = None
    kind: str | None = None
    approval_id: str | None = None

    def to_record(self) -> dict[str, Any]:
        d: dict[str, Any] = {"effect": self.effect, "risk": self.risk}
        if self.reason is not None:
            d["reason"] = self.reason
        if self.kind is not None:
            d["kind"] = self.kind
        if self.approval_id is not None:
            d["approvalId"] = self.approval_id
        return d


@dataclass
class ApprovalRequest:
    """Surfaced when a Policy decides require_approval."""

    approval_id: str
    kind: str
    risk: str = "low"
    reason: str | None = None
    action_preview: dict[str, Any] | None = None

    def to_record(self) -> dict[str, Any]:
        d: dict[str, Any] = {"approvalId": self.approval_id, "kind": self.kind, "risk": self.risk}
        if self.reason is not None:
            d["reason"] = self.reason
        if self.action_preview is not None:
            d["actionPreview"] = self.action_preview
        return d


@dataclass
class ApprovalDecision:
    """A human/automated resolution of an ApprovalRequest."""

    decision: str  # approve | reject
    reason: str | None = None
    by: str | None = None

    def to_record(self) -> dict[str, Any]:
        d: dict[str, Any] = {"decision": self.decision}
        if self.reason is not None:
            d["reason"] = self.reason
        if self.by is not None:
            d["by"] = self.by
        return d


# ---------------------------------------------------------------------------
# Policy protocol + factories
# ---------------------------------------------------------------------------


_ALLOW = PolicyDecision(effect="allow")


class Policy(Protocol):
    """Maps an :class:`Action` to a :class:`PolicyDecision`.

    Implementations MAY expose a ``budget`` attribute (``{"max_runtime_s": int}``
    / ``{"max_cost_usd": float}``, A5); the run driver reads it via
    ``getattr(policy, "budget", None)``.
    """

    def evaluate(self, action: Action) -> PolicyDecision: ...


def _command_matches(full: str, rule: str) -> bool:
    """True if *rule*'s whitespace tokens are a prefix of *full*'s tokens."""
    if rule == "*":
        return True
    ft = full.split()
    rt = rule.split()
    if len(rt) > len(ft):
        return False
    return ft[: len(rt)] == rt


class _ReadOnly:
    """Deny mutations (command / file_write / diff_apply); allow reads + tools."""

    budget: dict[str, Any] | None = None

    def evaluate(self, action: Action) -> PolicyDecision:
        if action.kind in ("command", "file_write", "diff_apply"):
            return PolicyDecision(
                effect="deny", kind=action.kind, risk="med",
                reason=f"read-only policy blocks {action.kind}",
            )
        return _ALLOW


class _Deny:
    budget: dict[str, Any] | None = None

    def __init__(self, *, reason: str = "denied by policy") -> None:
        self.reason = reason

    def evaluate(self, action: Action) -> PolicyDecision:
        return PolicyDecision(effect="deny", kind=action.kind, reason=self.reason)


class _RequireApprovalFor:
    budget: dict[str, Any] | None = None

    def __init__(self, *, commands: tuple[str, ...] = (),
                 files: tuple[str, ...] = ()) -> None:
        self.commands = tuple(commands)
        self.files = tuple(files)

    def evaluate(self, action: Action) -> PolicyDecision:
        if action.kind == "command" and action.command:
            for c in self.commands:
                if _command_matches(action.command, c):
                    return PolicyDecision(
                        effect="require_approval", kind=action.kind, risk="med",
                        reason=f"command requires approval: {c}",
                    )
        if action.kind in ("file_write", "diff_apply") and action.path:
            for f in self.files:
                if fnmatch.fnmatch(action.path, f):
                    return PolicyDecision(
                        effect="require_approval", kind=action.kind, risk="med",
                        reason=f"file requires approval: {f}",
                    )
        return _ALLOW


class _Compose:
    def __init__(self, *policies: Policy) -> None:
        self.policies = policies
        # Budget: later non-None wins (A5).
        self.budget: dict[str, Any] | None = None
        for p in policies:
            b = getattr(p, "budget", None)
            if b is not None:
                self.budget = b

    def evaluate(self, action: Action) -> PolicyDecision:
        decision: PolicyDecision = _ALLOW
        for p in self.policies:
            d = p.evaluate(action)
            if d.effect == "deny":
                return d
            if d.effect == "require_approval":
                decision = d
        return decision


class _Budget:
    def __init__(self, budget: dict[str, Any]) -> None:
        self.budget = budget

    def evaluate(self, action: Action) -> PolicyDecision:
        return _ALLOW


def read_only() -> Policy:
    """Allow reads/tools; deny commands, file writes, and diff applies."""
    return _ReadOnly()


def deny(*, reason: str = "denied by policy") -> Policy:
    """Deny every action."""
    return _Deny(reason=reason)


def require_approval_for(
    *, commands: tuple[str, ...] | list[str] = (),
    files: tuple[str, ...] | list[str] = (),
) -> Policy:
    """Require approval for commands (prefix match; ``"*"`` = any) and files
    (fnmatch, incl. ``**``)."""
    return _RequireApprovalFor(commands=tuple(commands), files=tuple(files))


def safe_local() -> Policy:
    """A sane local default: require approval for risky commands and protected paths."""
    return compose(
        require_approval_for(
            commands=["git push", "npm install", "pnpm add", "rm", "curl", "wget"],
            files=[".github/workflows/**", "migrations/**", ".env*"],
        )
    )


def compose(*policies: Policy) -> Policy:
    """Combine policies with deny > require_approval > allow precedence.

    An empty compose allows everything. Budgets: last non-None wins."""
    return _Compose(*policies)


def max_runtime_minutes(minutes: int) -> Policy:
    """Allow everything, with a runtime budget (seconds)."""
    return _Budget({"max_runtime_s": int(minutes) * 60})


def max_cost_usd(usd: float) -> Policy:
    """Allow everything, with a cost budget (USD)."""
    return _Budget({"max_cost_usd": float(usd)})
