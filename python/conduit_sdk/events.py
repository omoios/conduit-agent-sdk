"""Canonical typed event model for SessionUpdate normalization.

The 13 variant structs and the total ``normalize`` decoder are implemented in
Rust (``conduit_sdk._conduit_sdk``) and re-exported here — so the Python
boundary receives already-typed objects and the future napi-rs port wraps the
same definitions. This module keeps the wire-string enums, the JSON/enum
helpers, and the ``to_record`` / ``from_record`` serialization (a per-language
concern), plus the ``SessionEvent`` union alias.
"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any, Union

# Rust-backed variants + decoder.
from conduit_sdk._conduit_sdk import (
    AvailableCommands,
    ConfigUpdate,
    Done,
    ModeChange,
    Plan,
    RateLimit,
    SessionInfo,
    TextDelta,
    ThoughtDelta,
    ToolCallStart,
    ToolCallUpdate,
    Unknown,
    Usage,
    normalize,
)

__all__ = [
    "ToolKind",
    "ToolStatus",
    "StopReason",
    "TextDelta",
    "ThoughtDelta",
    "ToolCallStart",
    "ToolCallUpdate",
    "Plan",
    "AvailableCommands",
    "ModeChange",
    "ConfigUpdate",
    "Usage",
    "SessionInfo",
    "RateLimit",
    "Done",
    "Unknown",
    "SessionEvent",
    "normalize",
    "to_record",
    "from_record",
]


# ---------------------------------------------------------------------------
# Wire-value string Enums
#
# Kept in Python (not Rust) because PyO3 `eq_int` enums are integer-valued,
# which would break the wire-string semantics. Variant struct fields that hold
# a wire-enum value store the wire *string*; these str-enums compare equal to
# their own value, so `event.kind == ToolKind.READ` holds.
# ---------------------------------------------------------------------------


class ToolKind(str, Enum):
    READ = "read"
    EDIT = "edit"
    DELETE = "delete"
    MOVE = "move"
    SEARCH = "search"
    EXECUTE = "execute"
    THINK = "think"
    FETCH = "fetch"
    SWITCH_MODE = "switch_mode"
    OTHER = "other"


class ToolStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


class StopReason(str, Enum):
    END_TURN = "end_turn"
    MAX_TOKENS = "max_tokens"
    MAX_TURN_REQUESTS = "max_turn_requests"
    REFUSAL = "refusal"
    CANCELLED = "cancelled"


# ---------------------------------------------------------------------------
# Helpers (pure Python utilities; also exercised directly by the test suite)
# ---------------------------------------------------------------------------


def _to_enum(cls: type[Enum], value: str | None, default: Any = None) -> Any:
    """Try to convert *value* to *cls(value)*, returning *default* on failure."""
    if value is None:
        return default
    try:
        return cls(value)
    except (ValueError, KeyError):
        return default


def _safe_json(s: str | None) -> Any:
    """Parse *s* as JSON, returning the raw string on parse failure, or ``None``
    for ``None``/empty input. Never raises.
    """
    if s is None or s == "":
        return None
    try:
        return json.loads(s)
    except (ValueError, TypeError):
        return s


# ---------------------------------------------------------------------------
# Content-block text extraction (used by the public _decode_tool_output helper)
# ---------------------------------------------------------------------------


def _extract_text(node: Any, _key: str | None = None) -> list[str]:
    """Collect string leaves that live under a ``text``/``output`` key.

    ACP content blocks look like ``{"type": "content", "content": {"type":
    "text", "text": "..."}}`` — only the payload under ``text``/``output``
    is real output; ``type`` discriminants must be ignored.
    """
    out: list[str] = []
    if isinstance(node, str):
        if _key in ("text", "output"):
            out.append(node)
    elif isinstance(node, dict):
        for k, v in node.items():
            out.extend(_extract_text(v, _key=k))
    elif isinstance(node, (list, tuple)):
        for item in node:
            out.extend(_extract_text(item))
    return out


def _decode_tool_output(content_json: str | None) -> tuple[str | None, Any | None]:
    """Parse *content_json* (ACP tool_content) into ``(joined_text, raw_structure)``."""
    parsed = _safe_json(content_json)
    if parsed is None:
        return None, None
    if isinstance(parsed, str):
        # Parse failed or bare string — no meaningful output text
        return None, parsed
    parts = _extract_text(parsed)
    output = "\n".join(parts) if parts else None
    return output, parsed


# ---------------------------------------------------------------------------
# SessionEvent union
# ---------------------------------------------------------------------------

SessionEvent = Union[
    TextDelta,
    ThoughtDelta,
    ToolCallStart,
    ToolCallUpdate,
    Plan,
    AvailableCommands,
    ModeChange,
    ConfigUpdate,
    Usage,
    SessionInfo,
    RateLimit,
    Done,
    Unknown,
]

_ALL_VARIANTS: list[type] = [
    TextDelta,
    ThoughtDelta,
    ToolCallStart,
    ToolCallUpdate,
    Plan,
    AvailableCommands,
    ModeChange,
    ConfigUpdate,
    Usage,
    SessionInfo,
    RateLimit,
    Done,
    Unknown,
]


# ---------------------------------------------------------------------------
# Serialization (to_record / from_record) — a per-language concern.
#
# Note: `normalize` is Rust; these helpers operate on the Rust-backed variant
# structs via their getters / constructors. The round-trip invariant
# ``from_record(to_record(e)) == e`` is guaranteed by each variant's `__eq__`.
# ---------------------------------------------------------------------------

_VARIANT_TO_DISCRIMINATOR: dict[type, str] = {
    TextDelta: "text_delta",
    ThoughtDelta: "thought_delta",
    ToolCallStart: "tool_call_start",
    ToolCallUpdate: "tool_call_update",
    Plan: "plan",
    AvailableCommands: "available_commands",
    ModeChange: "mode_change",
    ConfigUpdate: "config_update",
    Usage: "usage",
    SessionInfo: "session_info",
    RateLimit: "rate_limit",
    Done: "done",
    Unknown: "unknown",
}

_VARIANT_FIELDS: dict[type, tuple[str, ...]] = {
    TextDelta: ("text",),
    ThoughtDelta: ("text",),
    ToolCallStart: ("tool_use_id", "title", "kind", "input", "status"),
    ToolCallUpdate: ("tool_use_id", "status", "output", "raw_content", "locations"),
    Plan: ("entries",),
    AvailableCommands: ("commands",),
    ModeChange: ("mode_id",),
    ConfigUpdate: ("config",),
    Usage: ("used", "size", "cost_amount", "cost_currency"),
    SessionInfo: ("title", "updated_at"),
    RateLimit: (
        "status",
        "resets_at",
        "rate_limit_type",
        "utilization",
        "is_using_overage",
        "surpassed_threshold",
    ),
    Done: ("stop_reason",),
    Unknown: ("kind", "raw"),
}


def to_record(event: SessionEvent) -> dict:
    """Serialize *event* to a JSON-safe dict with discriminator key ``event``."""
    cls = type(event)
    name = _VARIANT_TO_DISCRIMINATOR.get(cls, "unknown")
    d: dict[str, Any] = {"event": name}
    for f in _VARIANT_FIELDS.get(cls, ()):
        val = getattr(event, f)
        if isinstance(val, Enum):
            val = val.value
        d[f] = val
    return d


def from_record(record: dict) -> SessionEvent:
    """Deserialize a ``to_record`` dict back to a :class:`SessionEvent`.

    MUST satisfy ``from_record(to_record(e)) == e``.
    """
    event_type = record.get("event", "unknown")

    if event_type == "text_delta":
        return TextDelta(text=record.get("text", ""))
    elif event_type == "thought_delta":
        return ThoughtDelta(text=record.get("text", ""))
    elif event_type == "tool_call_start":
        return ToolCallStart(
            tool_use_id=record.get("tool_use_id", ""),
            title=record.get("title", ""),
            kind=_to_enum(ToolKind, record.get("kind"), None),
            input=record.get("input"),
            status=_to_enum(ToolStatus, record.get("status"), None),
        )
    elif event_type == "tool_call_update":
        return ToolCallUpdate(
            tool_use_id=record.get("tool_use_id", ""),
            status=_to_enum(ToolStatus, record.get("status"), None),
            output=record.get("output"),
            raw_content=record.get("raw_content"),
            locations=record.get("locations"),
        )
    elif event_type == "plan":
        return Plan(entries=record.get("entries", []))
    elif event_type == "available_commands":
        return AvailableCommands(commands=record.get("commands", []))
    elif event_type == "mode_change":
        return ModeChange(mode_id=record.get("mode_id", ""))
    elif event_type == "config_update":
        return ConfigUpdate(config=record.get("config"))
    elif event_type == "usage":
        return Usage(
            used=record.get("used"),
            size=record.get("size"),
            cost_amount=record.get("cost_amount"),
            cost_currency=record.get("cost_currency"),
        )
    elif event_type == "session_info":
        return SessionInfo(
            title=record.get("title"),
            updated_at=record.get("updated_at"),
        )
    elif event_type == "rate_limit":
        return RateLimit(
            status=record.get("status", ""),
            resets_at=record.get("resets_at", 0),
            rate_limit_type=record.get("rate_limit_type", ""),
            utilization=record.get("utilization", 0.0),
            is_using_overage=record.get("is_using_overage", False),
            surpassed_threshold=record.get("surpassed_threshold", 0.0),
        )
    elif event_type == "done":
        return Done(stop_reason=_to_enum(StopReason, record.get("stop_reason"), None))
    elif event_type == "unknown":
        return Unknown(kind=record.get("kind", "unknown"), raw=record.get("raw", {}))
    else:
        return Unknown(kind=event_type, raw=record)
