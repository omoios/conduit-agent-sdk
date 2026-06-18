"""High-level async Client for connecting to ACP agents.

Usage::

    async with Client(["claude", "--agent"]) as client:
        async for message in client.prompt("Hello!"):
            print(message.text())

With options::

    from conduit_sdk import AgentOptions, PermissionResultAllow

    async with Client(
        ["claude", "--agent"],
        options=AgentOptions(model="claude-sonnet-4-20250514"),
    ) as client:
        ...
"""

from __future__ import annotations

import json

from collections.abc import AsyncIterator
from typing import Any

from conduit_sdk._conduit_sdk import (
    ClientConfig,
    RustClient,
    RustControlProtocol,
    SessionUpdate,
    UpdateKind,
)
from conduit_sdk.exceptions import ConnectionError, HookBlockedError
from conduit_sdk.hooks import HookRunner, HookType
from conduit_sdk.options import AgentOptions
from conduit_sdk.query import Query
from conduit_sdk.registry import Registry
from conduit_sdk.session import Session
from conduit_sdk.types import Capabilities, HookContext, Message


class Client:
    """Async client for communicating with an ACP-compatible agent.

    Parameters
    ----------
    command:
        Shell command to spawn the agent process.
        Example: ``["claude", "--agent"]`` or ``["goose"]``.
    cwd:
        Working directory for the agent process.
    env:
        Additional environment variables for the agent.
    timeout:
        Connection timeout in seconds.
    options:
        Comprehensive agent configuration. Overrides ``cwd`` and ``env``
        if provided in both places.
    """

    def __init__(
        self,
        command: list[str],
        *,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        timeout: int = 30,
        options: AgentOptions | None = None,
    ) -> None:
        self._options = options

        # Options override individual params when provided.
        effective_cwd = cwd
        effective_env = env or {}
        if options is not None:
            if options.cwd is not None:
                effective_cwd = options.cwd
            if options.env:
                effective_env = {**effective_env, **options.env}

        self._config = ClientConfig(
            command=command,
            cwd=effective_cwd,
            env=effective_env,
            timeout_secs=timeout,
        )
        self._rust_client = RustClient(self._config)
        self._capabilities: Capabilities | None = None
        self._connected = False
        self._hooks = HookRunner()
        self._query: Query | None = None
        self._sdk_mcp_servers: list = []  # started McpSdkServerConfig instances
        self._default_session_id: str | None = None

    # -- Factory methods -----------------------------------------------------

    @classmethod
    async def from_registry(
        cls,
        agent_id: str,
        *,
        prefer: str | None = None,
        registry: Registry | None = None,
        timeout: int = 30,
        options: AgentOptions | None = None,
    ) -> Client:
        """Create a ``Client`` by looking up an agent in the ACP registry.

        Resolves *agent_id* to a shell command via the registry and returns
        an **unconnected** ``Client``.  Use :meth:`connect` or ``async with``
        to start the agent process.

        Parameters
        ----------
        agent_id:
            Registry identifier (e.g. ``"claude-acp"``).
        prefer:
            Preferred distribution type: ``"npx"``, ``"uvx"``, or ``"binary"``.
        registry:
            A pre-configured :class:`Registry` instance.  If ``None``, a
            default instance is created and fetched automatically.
        timeout:
            Connection timeout in seconds.
        options:
            Additional :class:`AgentOptions` for the client.
        """
        if registry is None:
            registry = Registry()
            await registry.fetch()

        cmd, env = await registry.resolve_command(agent_id, prefer=prefer)

        # Merge registry env with any user-provided env.
        merged_env = dict(env)
        if options and options.env:
            merged_env.update(options.env)

        return cls(
            cmd,
            env=merged_env or None,
            timeout=timeout,
            options=options,
        )

    # -- Connection lifecycle ------------------------------------------------

    async def connect(self) -> Capabilities:
        """Spawn the agent and perform the ACP initialize handshake.

        Returns the agent's advertised capabilities.
        """
        # Wire the permission callback into Rust before connecting.
        if self._options is not None and self._options.can_use_tool is not None:
            self._rust_client.set_permission_callback(self._options.can_use_tool)
        # Wire the elicitation handler into Rust before connecting.
        if self._options is not None and self._options.elicitation_handler is not None:
            from conduit_sdk.elicitation import _make_elicitation_bridge

            bridge = _make_elicitation_bridge(self._options.elicitation_handler)
            self._rust_client.set_elicitation_callback(bridge)

        self._capabilities = await self._rust_client.connect()
        self._connected = True

        # Set up control protocol with Query if options have callbacks.
        if self._options is not None:
            self._protocol = RustControlProtocol()
            self._query = Query(
                self._protocol,
                can_use_tool=self._options.can_use_tool,
            )

        # Start any in-process SDK MCP servers so the agent can reach them.
        await self._start_sdk_mcp_servers()
        await self._dispatch_hook(
            HookType.Connected, command=self._config.command
        )
        return self._capabilities

    async def disconnect(self) -> None:
        """Terminate the agent subprocess and clean up."""
        if self._query is not None:
            await self._query.close()
            self._query = None
        if self._connected:
            await self._dispatch_hook(HookType.Disconnected)
            await self._rust_client.disconnect()
            self._connected = False
        await self._stop_sdk_mcp_servers()

    async def _start_sdk_mcp_servers(self) -> None:
        """Start in-process MCP servers from SDK tools so agents can call them."""
        if not self._options or not self._options.mcp_servers:
            return
        from conduit_sdk.tools import McpSdkServerConfig

        for cfg in self._options.mcp_servers.values():
            if isinstance(cfg, McpSdkServerConfig) and cfg.url is None:
                await cfg.start()
                self._sdk_mcp_servers.append(cfg)

    async def _stop_sdk_mcp_servers(self) -> None:
        for cfg in self._sdk_mcp_servers:
            await cfg.stop()
        self._sdk_mcp_servers.clear()

    # -- Lifecycle hooks -----------------------------------------------------

    async def _dispatch_hook(self, hook_type: HookType, **data: Any) -> HookContext | None:
        """Fire registered hooks of ``hook_type`` (no-op when none registered).

        Returns the resulting :class:`HookContext` (carrying any mutations a
        hook applied), or ``None`` when no hook of that type is registered.
        """
        if not self._hooks.has(hook_type):
            return None
        ctx = HookContext(hook_type=hook_type, data=dict(data))
        return await self._hooks.dispatch(hook_type, ctx)

    async def _on_prompt_submit(self, text: str | list, session_id: str | None) -> str | list:
        """Fire PromptSubmit hooks before a prompt is sent.

        A hook may rewrite the prompt (set ``text`` on the context; str prompts
        only) or block it (set ``blocked`` or return the ``"block"`` sentinel),
        in which case :class:`HookBlockedError` is raised.
        """
        result = await self._dispatch_hook(
            HookType.PromptSubmit,
            text=text if isinstance(text, str) else None,
            session_id=session_id,
        )
        if result is None:
            return text
        if result.get("blocked"):
            raise HookBlockedError("prompt blocked by a PromptSubmit hook")
        new_text = result.get("text")
        if isinstance(text, str) and isinstance(new_text, str):
            return new_text
        return text

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def capabilities(self) -> Capabilities | None:
        return self._capabilities

    @property
    def hooks(self) -> HookRunner:
        return self._hooks

    @property
    def options(self) -> AgentOptions | None:
        return self._options

    @property
    def query(self) -> Query | None:
        return self._query

    # -- Internal helpers ---------------------------------------------------

    @staticmethod
    def _prepare_prompt(text: str | list) -> tuple[str, str | None]:
        """Normalize prompt input to (text_str, content_json).

        Returns a plain text string (always used as fallback) and an optional
        JSON-serialized content block array for rich/multi-modal prompts.
        """
        if isinstance(text, str):
            return text, None
        # List of content blocks
        from conduit_sdk.types import _serialize_content_blocks

        # Extract a text fallback from the first text-like block
        fallback = ""
        for item in text:
            if isinstance(item, str):
                fallback = item
                break
            if hasattr(item, "text") and isinstance(getattr(item, "text"), str):
                fallback = item.text
                break
        return fallback, _serialize_content_blocks(text)

    # -- Prompting -----------------------------------------------------------

    async def _ensure_default_session(self) -> str:
        """Create the default session (carrying mcp_servers) on first use.

        The Rust core auto-creates a session without MCP servers; creating it
        here ensures SDK tools are passed to the agent on the common path.
        """
        if self._default_session_id is not None:
            return self._default_session_id
        cwd = self._options.cwd if self._options else None
        meta_json = self._options.to_meta_json() if self._options else None
        mcp_servers_json = self._options.to_mcp_servers_json() if self._options else None
        self._default_session_id = await self._rust_client.new_session(
            cwd, meta_json, mcp_servers_json
        )
        await self._dispatch_hook(
            HookType.SessionCreated, session_id=self._default_session_id
        )
        return self._default_session_id

    async def prompt(
        self,
        text: str | list,
        *,
        session_id: str | None = None,
    ) -> AsyncIterator[Message]:
        """Send a prompt to the agent and stream back response messages.
        message contains the text received so far (not deltas).
        ----------
        text:
            The prompt text (string) or a list of content blocks
            (:class:`TextBlock`, :class:`ImageBlock`, :class:`AudioBlock`,
            :class:`ResourceLinkBlock`, :class:`EmbeddedResourceBlock`, or plain strings).
        session_id:
            Optional session ID. If ``None``, uses the client's default
            session (auto-created on first prompt).
        """
        if not self._connected:
            raise ConnectionError("client is not connected \u2014 call connect() first")
        if session_id is None:
            session_id = await self._ensure_default_session()
        text = await self._on_prompt_submit(text, session_id)
        text_str, content_json = self._prepare_prompt(text)
        messages = await self._rust_client.prompt(text_str, session_id, content_json)
        for msg in messages:
            yield msg

    async def prompt_stream(
        self,
        text: str | list,
        *,
        session_id: str | None = None,
    ) -> AsyncIterator[SessionUpdate]:
        """Send a prompt and yield real-time :class:`SessionUpdate` objects.
        (text deltas, thought deltas, tool use start/end) as it arrives.
        ----------
        text:
            The prompt text (string) or a list of content blocks.
        session_id:
            Optional session ID. If ``None``, uses the client's default
            session (auto-created on first prompt).
        """
        if not self._connected:
            raise ConnectionError("client is not connected \u2014 call connect() first")
        if session_id is None:
            session_id = await self._ensure_default_session()
        text = await self._on_prompt_submit(text, session_id)
        text_str, content_json = self._prepare_prompt(text)
        store = self._options.session_store if self._options else None
        await self._rust_client.send_prompt(text_str, session_id, content_json)
        while True:
            update = await self._rust_client.recv_update()
            if update is None:
                break
            yield update
            if store is not None:
                await store.append_update(session_id, self._record_update(update))
            kind = update.kind
            if kind == UpdateKind.ToolUseStart:
                await self._dispatch_hook(
                    HookType.PreToolUse,
                    tool_name=update.tool_name,
                    tool_input=update.tool_input,
                    tool_use_id=update.tool_use_id,
                )
            elif kind == UpdateKind.ToolUseEnd:
                await self._dispatch_hook(
                    HookType.PostToolUse,
                    tool_use_id=update.tool_use_id,
                    tool_status=update.tool_status,
                )
            elif kind == UpdateKind.Done:
                # A Done update is terminal — the agent's turn has ended.
                await self._dispatch_hook(HookType.Stop, stop_reason=update.stop_reason)
                break

    async def prompt_sync(
        self, text: str | list, *, session_id: str | None = None
    ) -> list[Message]:
        """Send a prompt and collect all response messages (non-streaming)."""
        return [msg async for msg in self.prompt(text, session_id=session_id)]

    @staticmethod
    def _record_update(update: SessionUpdate) -> dict[str, Any]:
        """Serialize a streaming ``SessionUpdate`` into a JSON-safe store record."""
        record: dict[str, Any] = {"kind": str(update.kind)}
        for attr in (
            "text", "tool_name", "tool_use_id", "tool_kind",
            "tool_status", "tool_input", "stop_reason", "mode_id",
        ):
            value = getattr(update, attr, None)
            if value is not None:
                record[attr] = value if isinstance(value, (str, int, float, bool)) else str(value)
        for attr in (
            "commands_json", "config_json", "plan_json", "usage_json",
            "rate_limit_json", "session_info_json",
        ):
            value = getattr(update, attr, None)
            if value:
                try:
                    record[attr] = json.loads(value)
                except (TypeError, ValueError):
                    record[attr] = value
        return record

    # -- Skill activation ---------------------------------------------------

    @staticmethod
    def _normalize_command(command: str) -> str:
        """Ensure a command string starts with ``/``."""
        command = command.strip()
        if not command.startswith("/"):
            command = f"/{command}"
        return command

    async def activate_skill(
        self,
        command: str,
        *,
        session_id: str | None = None,
    ) -> str:
        """Activate a single slash command (skill) and return the response text.

        The command is normalized to include the ``/`` prefix if missing.

        Parameters
        ----------
        command:
            Slash command to activate (e.g. ``"/help"`` or ``"help"``).
        session_id:
            Optional session ID. If ``None``, uses the default session.

        Returns
        -------
        str
            The collected text response from the agent.

        Example
        -------
        ::

            text = await client.activate_skill("/help")
            text = await client.activate_skill("compact")  # auto-prefixed
        """
        normalized = self._normalize_command(command)
        messages = await self.prompt_sync(normalized, session_id=session_id)
        return "".join(msg.text() for msg in messages)

    async def activate_skills(
        self,
        commands: list[str],
        *,
        session_id: str | None = None,
    ) -> list:
        """Activate multiple slash commands sequentially and return results.

        Each command is sent as a separate prompt. Results are collected in
        order, one :class:`~conduit_sdk.types.SkillResult` per command.

        Parameters
        ----------
        commands:
            List of slash commands (e.g. ``["/help", "/compact"]``).
            The ``/`` prefix is added automatically if missing.
        session_id:
            Optional session ID. If ``None``, uses the default session.

        Returns
        -------
        list[SkillResult]
            One result per command, in order.

        Example
        -------
        ::

            results = await client.activate_skills(["/help", "compact", "/cost"])
            for r in results:
                print(f"{r.command}: {r.text[:80]}")
        """
        from conduit_sdk.types import SkillResult

        results: list[SkillResult] = []
        for cmd in commands:
            normalized = self._normalize_command(cmd)
            try:
                text = await self.activate_skill(normalized, session_id=session_id)
                results.append(SkillResult(command=normalized, text=text, success=True))
            except Exception as exc:  # noqa: BLE001
                results.append(
                    SkillResult(
                        command=normalized,
                        text="",
                        success=False,
                        error=str(exc),
                    )
                )
        return results

    # -- Control protocol methods -------------------------------------------

    async def interrupt(self, session_id: str | None = None) -> None:
        """Send an interrupt/cancel to stop the agent's current operation.

        Parameters
        ----------
        session_id:
            If given, sends an ACP CancelNotification for that session.
            Otherwise falls back to the control-protocol interrupt.
        """
        if session_id is not None:
            await self._rust_client.cancel_session(session_id)
        elif self._query is not None:
            await self._query.interrupt()

    async def set_permission_mode(self, mode: str) -> None:
        """Change the permission mode mid-session."""
        if self._query is not None:
            await self._query.set_permission_mode(mode)

    async def set_model(self, model: str) -> None:
        """Change the model mid-session."""
        if self._query is not None:
            await self._query.set_model(model)

    async def cancel(self, session_id: str) -> None:
        """Cancel a running prompt in the given session (ACP CancelNotification)."""
        await self._rust_client.cancel_session(session_id)


    async def delete_session(self, session_id: str) -> dict:
        """Delete a session from the agent's session list.

        Returns the result as a dict (typically empty on success).
        """
        import json
        result_json = await self._rust_client.delete_session(session_id)
        await self._dispatch_hook(HookType.SessionDestroyed, session_id=session_id)
        return json.loads(result_json)

    async def set_config(self, session_id: str, config_id: str, value: str) -> dict:
        """Set a config option on a session. Returns the response as a dict."""
        import json
        result_json = await self._rust_client.set_config_option(session_id, config_id, value)
        return json.loads(result_json)

    async def fork_session(self, session_id: str, cwd: str | None = None) -> Session:
        """Fork a session, creating a new session with shared history.

        Returns a new :class:`Session` bound to the forked session ID.
        """
        new_sid = await self._rust_client.fork_session(session_id, cwd)
        session = Session(self)
        session._session_id = new_sid
        return session

    async def list_sessions(self, cwd: str | None = None) -> list[dict]:
        """List available sessions from the agent. Returns a list of dicts."""
        import json
        result_json = await self._rust_client.list_sessions(cwd)
        return json.loads(result_json)

    async def resume_session(self, session_id: str, cwd: str | None = None) -> Session:
        """Resume an existing agent-side session.

        Returns a :class:`Session` bound to the resumed session ID.
        """
        resumed_sid = await self._rust_client.resume_session(session_id, cwd)
        session = Session(self)
        session._session_id = resumed_sid
        return session

    @property
    async def agent_info(self) -> dict | None:
        """Return agent server info (name, version, title) or None."""
        import json
        info_json = await self._rust_client.agent_info()
        if info_json is None:
            return None
        return json.loads(info_json)

    # -- Session shortcuts ---------------------------------------------------

    async def new_session(self, cwd: str | None = None) -> Session:
        """Create a new conversation session on this client.

        Passes system_prompt, model, max_turns, and MCP server configs
        from :attr:`options` into the ACP ``newSession`` request.
        """
        meta_json = None
        mcp_servers_json = None
        if self._options is not None:
            meta_json = self._options.to_meta_json()
            mcp_servers_json = self._options.to_mcp_servers_json()
        session = Session(self)
        await session.create(cwd, meta_json=meta_json, mcp_servers_json=mcp_servers_json)
        await self._dispatch_hook(
            HookType.SessionCreated, session_id=session.session_id
        )
        return session

    # -- Context manager -----------------------------------------------------

    async def __aenter__(self) -> Client:
        await self.connect()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.disconnect()

    def __repr__(self) -> str:
        status = "connected" if self._connected else "disconnected"
        opts = f", options={self._options!r}" if self._options else ""
        return f"Client(command={self._config.command!r}, {status}{opts})"
