"""Canonical typed event model for SessionUpdate normalization.

Transforms the flat Rust-backed ``SessionUpdate`` object (``UpdateKind`` +
JSON-string fields) into a typed frozen-dataclass union (``SessionEvent``),
with pure ``normalize()``, ``to_record()``, and ``from_record()`` for
serialization.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, fields as _fields
from enum import Enum
from typing import Any, Union


# ---------------------------------------------------------------------------
# Wire-value string Enums
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
# Helpers
# ---------------------------------------------------------------------------


def _to_enum(cls: type[Enum], value: str | None, default: Any = None) -> Any:
    """Try to convert *value* to *cls(value)*, returning *default* on failure."""
    if value is None:
        return default
    try:
        return cls(value)
    except (ValueError, TypeError):
        return default


def _safe_json(s: str | None) -> Any:
    """Parse *s* as JSON, returning the raw string on parse failure, or ``None``
    when *s* is ``None`` or empty."""
    if not s:
        return None
    try:
        return json.loads(s)
    except (json.JSONDecodeError, TypeError, ValueError):
        return s


# ---------------------------------------------------------------------------
# Content-block text extraction (ported from toolview._extract_text)
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
# SessionEvent variants
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TextDelta:
    text: str


@dataclass(frozen=True)
class ThoughtDelta:
    text: str


@dataclass(frozen=True)
class ToolCallStart:
    tool_use_id: str
    title: str
    kind: ToolKind | None
    input: Any
    status: ToolStatus | None


@dataclass(frozen=True)
class ToolCallUpdate:
    tool_use_id: str
    status: ToolStatus | None
    output: str | None
    raw_content: Any | None
    locations: list | None


@dataclass(frozen=True)
class Plan:
    entries: list


@dataclass(frozen=True)
class AvailableCommands:
    commands: list


@dataclass(frozen=True)
class ModeChange:
    mode_id: str


@dataclass(frozen=True)
class ConfigUpdate:
    config: Any


@dataclass(frozen=True)
class Usage:
    used: int | None
    size: int | None
    cost_amount: float | None
    cost_currency: str | None


@dataclass(frozen=True)
class SessionInfo:
    title: str | None
    updated_at: str | None


@dataclass(frozen=True)
class RateLimit:
    status: str
    resets_at: int
    rate_limit_type: str
    utilization: float
    is_using_overage: bool
    surpassed_threshold: float


@dataclass(frozen=True)
class Done:
    stop_reason: StopReason | None


@dataclass(frozen=True)
class Unknown:
    kind: str
    raw: dict


# ---------------------------------------------------------------------------
# Union type
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
# normalize  (total, pure)
# ---------------------------------------------------------------------------


def normalize(update: Any) -> SessionEvent:
    """Map a ``SessionUpdate`` (Rust PyO3 duck-typed object) to a typed
    :class:`SessionEvent` union member.

    TOTAL — never raises.  On any unexpected error the result is an
    ``Unknown`` variant with ``kind="normalize_error"``.
    """
    from conduit_sdk._conduit_sdk import UpdateKind

    try:
        kind = update.kind
    except Exception:
        return Unknown(kind="normalize_error", raw={})

    try:
        if kind == UpdateKind.TextDelta:
            _text = getattr(update, 'text', None)
            return TextDelta(text=_text or "")
        elif kind == UpdateKind.ThoughtDelta:
            _text = getattr(update, 'text', None)
            return ThoughtDelta(text=_text or "")
        elif kind == UpdateKind.ToolUseStart:
            return ToolCallStart(
                tool_use_id=getattr(update, 'tool_use_id', None) or "",
                title=getattr(update, 'tool_name', None) or "",
                kind=_to_enum(ToolKind, getattr(update, 'tool_kind', None), None),
                input=_safe_json(getattr(update, 'tool_input', None)),
                status=_to_enum(ToolStatus, getattr(update, 'tool_status', None), None),
            )
        elif kind == UpdateKind.ToolUseUpdate:
            output, raw_content = _decode_tool_output(getattr(update, 'tool_content', None))
            raw_locs = _safe_json(getattr(update, 'tool_locations', None))
            locations = raw_locs if isinstance(raw_locs, list) else None
            return ToolCallUpdate(
                tool_use_id=getattr(update, 'tool_use_id', None) or "",
                status=_to_enum(ToolStatus, getattr(update, 'tool_status', None), None),
                output=output,
                raw_content=raw_content,
                locations=locations,
            )
        elif kind == UpdateKind.ToolUseEnd:
            return ToolCallUpdate(
                tool_use_id=getattr(update, 'tool_use_id', None) or "",
                status=None,
                output=None,
                raw_content=None,
                locations=None,
            )
        elif kind == UpdateKind.Plan:
            entries = _safe_json(getattr(update, 'plan_json', None))
            return Plan(entries=entries if isinstance(entries, list) else [])
        elif kind == UpdateKind.CommandsUpdate:
            cmds = _safe_json(getattr(update, 'commands_json', None))
            return AvailableCommands(commands=cmds if isinstance(cmds, list) else [])
        elif kind == UpdateKind.ModeChange:
            return ModeChange(mode_id=getattr(update, 'mode_id', None) or "")
        elif kind == UpdateKind.ConfigUpdate:
            return ConfigUpdate(config=_safe_json(getattr(update, 'config_json', None)))
        elif kind == UpdateKind.Usage:
            raw = _safe_json(getattr(update, 'usage_json', None))
            u = raw if isinstance(raw, dict) else {}
            cost = u.get("cost") or {}
            return Usage(
                used=u.get("used"),
                size=u.get("size"),
                cost_amount=cost.get("amount"),
                cost_currency=cost.get("currency"),
            )
        elif kind == UpdateKind.SessionInfo:
            raw = _safe_json(getattr(update, 'session_info_json', None))
            i = raw if isinstance(raw, dict) else {}
            return SessionInfo(title=i.get("title"), updated_at=i.get("updated_at"))
        elif kind == UpdateKind.RateLimit:
            raw = _safe_json(getattr(update, 'rate_limit_json', None))
            data = raw if isinstance(raw, dict) else {}
            params = data.get("params") or {}
            info = params.get("rate_limit_info", params)
            return RateLimit(
                status=info.get("status", ""),
                resets_at=info.get("resetsAt", 0),
                rate_limit_type=info.get("rateLimitType", ""),
                utilization=info.get("utilization", 0.0),
                is_using_overage=info.get("isUsingOverage", False),
                surpassed_threshold=info.get("surpassedThreshold", 0.0),
            )
        elif kind == UpdateKind.Done:
            return Done(stop_reason=_to_enum(StopReason, getattr(update, 'stop_reason', None), None))
        elif kind == UpdateKind.Error:
            return Unknown(kind="error", raw={"message": getattr(update, 'error', None) or ""})
        else:
            return Unknown(kind=str(kind), raw={})
    except Exception:
        return Unknown(kind="normalize_error", raw={"kind": str(getattr(update, 'kind', None))})


# ---------------------------------------------------------------------------
# Serialization
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


def to_record(event: SessionEvent) -> dict:
    """Serialize *event* to a JSON-safe dict with discriminator key ``event``."""
    name = _VARIANT_TO_DISCRIMINATOR.get(type(event), "unknown")
    d: dict[str, Any] = {"event": name}
    for f in _fields(event):
        val = getattr(event, f.name)
        if isinstance(val, Enum):
            val = val.value
        d[f.name] = val
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
