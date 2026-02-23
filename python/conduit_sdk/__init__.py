"""conduit-agent-sdk — General-purpose Python SDK for the Agent Client Protocol (ACP).

Works with any ACP-compatible coding agent: Claude Code, Gemini CLI, Goose, etc.

Quick start (registry-based)::

    import asyncio
    from conduit_sdk import query

    async def main():
        async for message in query(prompt="Hello!", agent="claude-acp"):
            print(message.text())

    asyncio.run(main())

With explicit client::

    from conduit_sdk import Client

    async with await Client.from_registry("claude-acp") as client:
        async for message in client.prompt("Hello!"):
            print(message.text())

Manual command (no registry)::

    from conduit_sdk import Client

    async with Client(["claude", "--agent"]) as client:
        async for message in client.prompt("Hello!"):
            print(message.text())
"""

from __future__ import annotations

# Version from the Rust native module.
from conduit_sdk._conduit_sdk import __version__

# Public API — high-level classes.
from conduit_sdk.activate import query
from conduit_sdk.client import Client
from conduit_sdk.exceptions import (
    AgentNotFoundError,
    CancelledError,
    ConduitError,
    ConnectionError,
    DistributionError,
    HookError,
    PermissionError,
    ProtocolError,
    ProxyError,
    RegistryError,
    RuntimeNotFoundError,
    SessionError,
    TimeoutError,
    ToolError,
    TransportError,
)
from conduit_sdk.hooks import HookRunner, HookType, hook
from conduit_sdk.options import AgentOptions
from conduit_sdk.permissions import (
    PermissionResult,
    PermissionResultAllow,
    PermissionResultDeny,
    ToolPermissionContext,
    allow_all,
    console_approve,
    deny_all,
)
from conduit_sdk.proxy import ContextInjector, Proxy, ProxyChain, ResponseFilter
from conduit_sdk.query import Query
from conduit_sdk.registry import AgentInfo, Registry
from conduit_sdk.session import Session
from conduit_sdk.tools import McpSdkServerConfig, create_mcp_server, create_sdk_mcp_server, tool
from conduit_sdk.types import (
    Capabilities,
    ClientConfig,
    ContentBlock,
    ContentType,
    ControlMessage,
    ControlResponse,
    HookContext,
    Message,
    MessageRole,
    PermissionRequest,
    PermissionResponse,
    ResultMessage,
    SessionUpdate,
    StreamEvent,
    TextBlock,
    ThinkingBlock,
    ToolDefinition,
    ToolResultBlock,
    ToolSchema,
    ToolUseBlock,
    UpdateKind,
)

__all__ = [
    # Core
    "__version__",
    "Client",
    "Session",
    "Query",
    # Registry & activation
    "query",
    "Registry",
    "AgentInfo",
    # Options & Permissions
    "AgentOptions",
    "PermissionResult",
    "PermissionResultAllow",
    "PermissionResultDeny",
    "ToolPermissionContext",
    "allow_all",
    "deny_all",
    "console_approve",
    # Tools
    "tool",
    "create_mcp_server",
    "create_sdk_mcp_server",
    "McpSdkServerConfig",
    # Hooks
    "hook",
    "HookRunner",
    "HookType",
    # Proxy
    "Proxy",
    "ProxyChain",
    "ContextInjector",
    "ResponseFilter",
    # Types — original
    "Capabilities",
    "ClientConfig",
    "ContentBlock",
    "ContentType",
    "HookContext",
    "Message",
    "MessageRole",
    "SessionUpdate",
    "ToolDefinition",
    "ToolSchema",
    "UpdateKind",
    # Types — control protocol
    "ControlMessage",
    "ControlResponse",
    "PermissionRequest",
    "PermissionResponse",
    "ResultMessage",
    "StreamEvent",
    # Types — content block helpers
    "TextBlock",
    "ThinkingBlock",
    "ToolUseBlock",
    "ToolResultBlock",
    # Exceptions
    "ConduitError",
    "ConnectionError",
    "SessionError",
    "TransportError",
    "ProtocolError",
    "ToolError",
    "HookError",
    "ProxyError",
    "TimeoutError",
    "CancelledError",
    "PermissionError",
    # Registry exceptions
    "RegistryError",
    "AgentNotFoundError",
    "DistributionError",
    "RuntimeNotFoundError",
]
