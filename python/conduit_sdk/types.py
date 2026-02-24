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
    # Rate limit
    "RateLimitInfo",
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


# ---------------------------------------------------------------------------
# Rich content block types — for multi-modal prompts (Phase 3 Item 10)
# ---------------------------------------------------------------------------


@dataclass
class ImageBlock:
    """An image content block for multi-modal prompts.

    Parameters
    ----------
    data:
        Base64-encoded image data.
    mime_type:
        MIME type (e.g. ``"image/png"``, ``"image/jpeg"``).
    uri:
        Optional URI for the image source.
    """

    data: str
    mime_type: str
    uri: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "type": "image",
            "data": self.data,
            "mimeType": self.mime_type,
        }
        if self.uri is not None:
            d["uri"] = self.uri
        return d


@dataclass
class AudioBlock:
    """An audio content block for multi-modal prompts.

    Parameters
    ----------
    data:
        Base64-encoded audio data.
    mime_type:
        MIME type (e.g. ``"audio/wav"``, ``"audio/mp3"``).
    """

    data: str
    mime_type: str

    def to_dict(self) -> dict[str, Any]:
        return {"type": "audio", "data": self.data, "mimeType": self.mime_type}


@dataclass
class ResourceLinkBlock:
    """A resource link content block — references a resource by URI.

    Parameters
    ----------
    uri:
        The URI of the resource.
    name:
        Optional display name.
    description:
        Optional description.
    mime_type:
        Optional MIME type hint.
    """

    uri: str
    name: str | None = None
    description: str | None = None
    mime_type: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": "resource_link", "uri": self.uri}
        if self.name is not None:
            d["name"] = self.name
        if self.description is not None:
            d["description"] = self.description
        if self.mime_type is not None:
            d["mimeType"] = self.mime_type
        return d


@dataclass
class EmbeddedResourceBlock:
    """An embedded resource content block — includes full resource contents inline.

    Parameters
    ----------
    uri:
        The URI identifying the resource.
    text:
        Text content of the resource (for text resources).
    mime_type:
        Optional MIME type.
    blob:
        Base64-encoded binary content (for non-text resources).
    """

    uri: str
    text: str | None = None
    mime_type: str | None = None
    blob: str | None = None

    def to_dict(self) -> dict[str, Any]:
        resource: dict[str, Any] = {"uri": self.uri}
        if self.text is not None:
            resource["text"] = self.text
        if self.mime_type is not None:
            resource["mimeType"] = self.mime_type
        if self.blob is not None:
            resource["blob"] = self.blob
        return {"type": "resource", "resource": resource}


@dataclass
class RateLimitInfo:
    """Rate limit event data from the agent.

    This is surfaced when the agent sends a ``rate_limit_event``
    extension notification (e.g. from Claude).

    Parameters
    ----------
    status:
        Rate limit status (e.g. ``"allowed_warning"``).
    resets_at:
        Unix timestamp when the rate limit resets.
    rate_limit_type:
        Type of rate limit (e.g. ``"seven_day"``).
    utilization:
        Current utilization as a float (0.0–1.0).
    is_using_overage:
        Whether overage is being consumed.
    surpassed_threshold:
        The threshold that was surpassed (e.g. 0.75).
    raw_json:
        The full raw JSON string for any extra fields.
    """

    status: str = ""
    resets_at: int = 0
    rate_limit_type: str = ""
    utilization: float = 0.0
    is_using_overage: bool = False
    surpassed_threshold: float = 0.0
    raw_json: str = ""

    @classmethod
    def from_json(cls, json_str: str) -> "RateLimitInfo":
        """Parse from the JSON string in ``SessionUpdate.rate_limit_json``."""
        import json

        data = json.loads(json_str)
        params = data.get("params", {})
        info = params.get("rate_limit_info", params)
        return cls(
            status=info.get("status", ""),
            resets_at=info.get("resetsAt", 0),
            rate_limit_type=info.get("rateLimitType", ""),
            utilization=info.get("utilization", 0.0),
            is_using_overage=info.get("isUsingOverage", False),
            surpassed_threshold=info.get("surpassedThreshold", 0.0),
            raw_json=json_str,
        )


# Union type for prompt content
PromptContent = (
    str
    | TextBlock
    | ImageBlock
    | AudioBlock
    | ResourceLinkBlock
    | EmbeddedResourceBlock
)


def _serialize_content_blocks(content: list[PromptContent]) -> str:
    """Serialize a list of content blocks to JSON for the Rust layer.

    Accepts a mix of strings and typed block objects.
    """
    import json

    blocks: list[dict[str, Any]] = []
    for item in content:
        if isinstance(item, str):
            blocks.append({"type": "text", "text": item})
        elif isinstance(item, TextBlock):
            blocks.append({"type": "text", "text": item.text})
        elif hasattr(item, "to_dict"):
            blocks.append(item.to_dict())
        else:
            raise TypeError(f"unsupported content block type: {type(item).__name__}")
    return json.dumps(blocks)
