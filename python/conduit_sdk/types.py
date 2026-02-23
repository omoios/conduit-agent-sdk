"""Public type definitions for conduit-agent-sdk.

Re-exports native Rust types from ``_conduit_sdk`` and adds
pure-Python convenience types where needed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Re-export Rust-defined types so the rest of the Python layer
# (and end-users) can import from ``conduit_sdk.types``.
from conduit_sdk._conduit_sdk import (
    Capabilities,
    ClientConfig,
    ContentBlock,
    ContentType,
    ControlMessage,
    ControlResponse,
    Message,
    MessageRole,
    PermissionRequest,
    PermissionResponse,
    ResultMessage,
    SessionUpdate,
    StreamEvent,
    ToolDefinition,
    UpdateKind,
)

__all__ = [
    # Original types
    "Capabilities",
    "ClientConfig",
    "ContentBlock",
    "ContentType",
    "Message",
    "MessageRole",
    "SessionUpdate",
    "ToolDefinition",
    "UpdateKind",
    "ToolSchema",
    "HookContext",
    # New control protocol types
    "ControlMessage",
    "ControlResponse",
    "PermissionRequest",
    "PermissionResponse",
    "ResultMessage",
    "StreamEvent",
    # Content block helpers
    "TextBlock",
    "ThinkingBlock",
    "ToolUseBlock",
    "ToolResultBlock",
]


@dataclass
class ToolSchema:
    """Convenience wrapper for building JSON Schema input definitions."""

    type: str = "object"
    properties: dict[str, Any] = field(default_factory=dict)
    required: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        import json

        return json.dumps(
            {
                "type": self.type,
                "properties": self.properties,
                "required": self.required,
            }
        )


@dataclass
class HookContext:
    """Context object passed to lifecycle hook callbacks."""

    hook_type: str
    data: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self.data[key] = value


# ---------------------------------------------------------------------------
# Typed content block helpers — convenience constructors
# ---------------------------------------------------------------------------


@dataclass
class TextBlock:
    """A text content block."""

    text: str

    def to_content_block(self) -> ContentBlock:
        return ContentBlock(ContentType.Text, text=self.text)


@dataclass
class ThinkingBlock:
    """A thinking/reasoning content block."""

    thinking: str

    def to_content_block(self) -> ContentBlock:
        return ContentBlock(ContentType.Text, text=self.thinking)


@dataclass
class ToolUseBlock:
    """A tool use content block."""

    tool_name: str
    tool_input: str
    tool_use_id: str | None = None

    def to_content_block(self) -> ContentBlock:
        return ContentBlock(
            ContentType.ToolUse,
            tool_name=self.tool_name,
            tool_input=self.tool_input,
            tool_use_id=self.tool_use_id,
        )


@dataclass
class ToolResultBlock:
    """A tool result content block."""

    tool_use_id: str
    text: str | None = None

    def to_content_block(self) -> ContentBlock:
        return ContentBlock(
            ContentType.ToolResult,
            text=self.text,
            tool_use_id=self.tool_use_id,
        )
