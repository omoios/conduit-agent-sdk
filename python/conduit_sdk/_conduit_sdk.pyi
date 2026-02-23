"""Type stubs for the Rust native extension module ``_conduit_sdk``."""

from __future__ import annotations

from enum import IntEnum
from typing import Any

__version__: str

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

class MessageRole(IntEnum):
    User = ...
    Assistant = ...
    System = ...
    Tool = ...

class ContentType(IntEnum):
    Text = ...
    ToolUse = ...
    ToolResult = ...
    Image = ...
    Error = ...

class UpdateKind(IntEnum):
    TextDelta = ...
    ToolUseStart = ...
    ToolUseEnd = ...
    Done = ...
    Error = ...

class HookType(IntEnum):
    PreToolUse = ...
    PostToolUse = ...
    PromptSubmit = ...
    ResponseReceived = ...
    SessionCreated = ...
    SessionDestroyed = ...
    Connected = ...
    Disconnected = ...

class Capabilities:
    sessions: bool
    tools: bool
    proxy: bool
    modes: list[str]
    models: list[str]

    def __init__(
        self,
        sessions: bool = False,
        tools: bool = False,
        proxy: bool = False,
        modes: list[str] | None = None,
        models: list[str] | None = None,
    ) -> None: ...
    def __repr__(self) -> str: ...

class ContentBlock:
    content_type: ContentType
    text: str | None
    tool_name: str | None
    tool_input: str | None
    tool_use_id: str | None

    def __init__(
        self,
        content_type: ContentType,
        text: str | None = None,
        tool_name: str | None = None,
        tool_input: str | None = None,
        tool_use_id: str | None = None,
    ) -> None: ...
    def __repr__(self) -> str: ...

class Message:
    role: MessageRole
    content: list[ContentBlock]
    session_id: str | None

    def __init__(
        self,
        role: MessageRole,
        content: list[ContentBlock],
        session_id: str | None = None,
    ) -> None: ...
    def text(self) -> str: ...
    def __repr__(self) -> str: ...

class SessionUpdate:
    kind: UpdateKind
    text: str | None
    tool_name: str | None
    tool_input: str | None
    tool_use_id: str | None
    error: str | None

    def __init__(
        self,
        kind: UpdateKind,
        text: str | None = None,
        tool_name: str | None = None,
        tool_input: str | None = None,
        tool_use_id: str | None = None,
        error: str | None = None,
    ) -> None: ...
    def __repr__(self) -> str: ...

class ClientConfig:
    command: list[str]
    cwd: str | None
    env: dict[str, str]
    timeout_secs: int

    def __init__(
        self,
        command: list[str],
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout_secs: int = 30,
    ) -> None: ...
    def __repr__(self) -> str: ...

class ToolDefinition:
    name: str
    description: str
    input_schema: str

    def __init__(self, name: str, description: str, input_schema: str) -> None: ...
    def __repr__(self) -> str: ...

class ProxyConfig:
    name: str
    command: list[str]

    def __init__(self, name: str, command: list[str]) -> None: ...
    def __repr__(self) -> str: ...

# ---------------------------------------------------------------------------
# Permission types
# ---------------------------------------------------------------------------

class PermissionRequest:
    tool_name: str
    tool_input: str
    tool_use_id: str | None
    session_id: str | None

    def __init__(
        self,
        tool_name: str,
        tool_input: str,
        tool_use_id: str | None = None,
        session_id: str | None = None,
    ) -> None: ...
    def __repr__(self) -> str: ...

class PermissionResponse:
    decision: str
    reason: str | None

    def __init__(
        self,
        decision: str,
        reason: str | None = None,
    ) -> None: ...
    def __repr__(self) -> str: ...

# ---------------------------------------------------------------------------
# Result and stream types
# ---------------------------------------------------------------------------

class ResultMessage:
    subtype: str
    duration_ms: int
    is_error: bool
    num_turns: int
    session_id: str
    total_cost_usd: float | None
    result: str | None

    def __init__(
        self,
        subtype: str,
        duration_ms: int,
        is_error: bool,
        num_turns: int,
        session_id: str,
        total_cost_usd: float | None = None,
        result: str | None = None,
    ) -> None: ...
    def __repr__(self) -> str: ...

class StreamEvent:
    uuid: str
    session_id: str
    event: str

    def __init__(self, uuid: str, session_id: str, event: str) -> None: ...
    def __repr__(self) -> str: ...

# ---------------------------------------------------------------------------
# Control protocol
# ---------------------------------------------------------------------------

class ControlMessage:
    request_id: str
    subtype: str
    data: str

    def __init__(self, request_id: str, subtype: str, data: str) -> None: ...
    def __repr__(self) -> str: ...

class ControlResponse:
    request_id: str
    subtype: str
    data: str

    def __init__(self, request_id: str, subtype: str, data: str) -> None: ...
    def __repr__(self) -> str: ...

class RustControlProtocol:
    def __init__(self) -> None: ...
    async def start(self, stdin_fd: int, stdout_fd: int) -> None: ...
    async def send_control_request(self, subtype: str, data: str) -> str: ...
    async def send_control_response(
        self, request_id: str, subtype: str, data: str
    ) -> None: ...
    async def recv_message(self) -> str | None: ...
    def set_permission_callback(self, callback: Any) -> None: ...
    def set_hook_callback(self, callback: Any) -> None: ...
    def set_mcp_callback(self, callback: Any) -> None: ...
    async def is_running(self) -> bool: ...
    async def stop(self) -> None: ...

# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class RustClient:
    def __init__(self, config: ClientConfig) -> None: ...
    async def connect(self) -> Capabilities: ...
    async def prompt(self, text: str) -> list[Message]: ...
    async def capabilities(self) -> Capabilities | None: ...
    async def disconnect(self) -> None: ...

# ---------------------------------------------------------------------------
# Session
# ---------------------------------------------------------------------------

class RustSessionManager:
    def __init__(self) -> None: ...
    async def create(self) -> str: ...
    async def load(self, session_id: str) -> str: ...
    async def fork(self, source_id: str) -> str: ...
    async def set_mode(self, session_id: str, mode: str) -> None: ...
    async def set_model(self, session_id: str, model: str) -> None: ...
    async def list_sessions(self) -> list[str]: ...

# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

class RustToolRegistry:
    def __init__(self) -> None: ...
    async def register(self, definition: ToolDefinition, callback: Any) -> None: ...
    async def unregister(self, name: str) -> None: ...
    async def list_tools(self) -> list[str]: ...
    async def invoke(self, name: str, input_json: str) -> str: ...

# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------

class RustHookDispatcher:
    def __init__(self) -> None: ...
    async def register(self, hook_type: HookType, callback: Any, priority: int = 0) -> None: ...
    async def dispatch(self, hook_type: HookType, context_json: str) -> str: ...
    async def clear(self, hook_type: HookType) -> None: ...

# ---------------------------------------------------------------------------
# Proxy
# ---------------------------------------------------------------------------

class RustProxyChain:
    def __init__(self) -> None: ...
    async def add(self, proxy: ProxyConfig) -> None: ...
    async def insert(self, index: int, proxy: ProxyConfig) -> None: ...
    async def list(self) -> list[ProxyConfig]: ...
    async def clear(self) -> None: ...
    async def build(self) -> None: ...
