"""Agent configuration options for conduit-agent-sdk.

Provides the ``AgentOptions`` dataclass for comprehensive agent
configuration including system prompt, model selection, permission
mode, tool allowlists, and MCP server configs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class AgentOptions:
    """Comprehensive configuration for an ACP agent connection.

    Parameters
    ----------
    system_prompt:
        Custom system instructions prepended to the agent's context.
    model:
        Model identifier to use (e.g. ``"claude-sonnet-4-20250514"``).
    permission_mode:
        Permission enforcement mode. One of:
        ``"default"``, ``"acceptEdits"``, ``"plan"``, ``"bypassPermissions"``.
    can_use_tool:
        Async callback invoked for each tool use. Receives
        ``(tool_name, tool_input, context)`` and must return a
        ``PermissionResult``.
    tools:
        List of built-in tool names available to the agent.
    allowed_tools:
        Tool name allowlist — only these tools may be used.
    disallowed_tools:
        Tool name blocklist — these tools are never allowed.
    mcp_servers:
        MCP server configurations keyed by server name. Values can be
        ``McpSdkServerConfig`` instances or raw dicts.
    max_turns:
        Maximum number of conversation turns before stopping.
    cwd:
        Working directory for the agent process.
    env:
        Additional environment variables passed to the agent.
    include_partial_messages:
        When ``True``, stream events are yielded as they arrive.
    hooks:
        Lifecycle hook configuration dict.
    """

    system_prompt: str | None = None
    model: str | None = None
    permission_mode: str | None = None
    can_use_tool: Callable | None = None
    tools: list[str] | None = None
    allowed_tools: list[str] = field(default_factory=list)
    disallowed_tools: list[str] = field(default_factory=list)
    mcp_servers: dict[str, Any] | None = None
    max_turns: int | None = None
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    include_partial_messages: bool = False
    hooks: dict | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize non-None fields to a dict for the control protocol."""
        result: dict[str, Any] = {}
        if self.system_prompt is not None:
            result["systemPrompt"] = self.system_prompt
        if self.model is not None:
            result["model"] = self.model
        if self.permission_mode is not None:
            result["permissionMode"] = self.permission_mode
        if self.tools is not None:
            result["tools"] = self.tools
        if self.allowed_tools:
            result["allowedTools"] = self.allowed_tools
        if self.disallowed_tools:
            result["disallowedTools"] = self.disallowed_tools
        if self.mcp_servers is not None:
            result["mcpServers"] = {
                name: (
                    srv.to_dict() if hasattr(srv, "to_dict") else srv
                )
                for name, srv in self.mcp_servers.items()
            }
        if self.max_turns is not None:
            result["maxTurns"] = self.max_turns
        if self.cwd is not None:
            result["cwd"] = self.cwd
        if self.env:
            result["env"] = self.env
        if self.include_partial_messages:
            result["includePartialMessages"] = True
        if self.hooks is not None:
            result["hooks"] = self.hooks
        return result
