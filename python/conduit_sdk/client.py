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

from collections.abc import AsyncIterator
from typing import Any

from conduit_sdk._conduit_sdk import (
    ClientConfig,
    RustClient,
    RustControlProtocol,
    SessionUpdate,
    UpdateKind,
)
from conduit_sdk.exceptions import ConnectionError
from conduit_sdk.hooks import HookRunner
from conduit_sdk.options import AgentOptions
from conduit_sdk.query import Query
from conduit_sdk.registry import Registry
from conduit_sdk.session import Session
from conduit_sdk.types import Capabilities, Message


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
        self._protocol: RustControlProtocol | None = None

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

        self._capabilities = await self._rust_client.connect()
        self._connected = True

        # Set up control protocol with Query if options have callbacks.
        if self._options is not None:
            self._protocol = RustControlProtocol()
            self._query = Query(
                self._protocol,
                can_use_tool=self._options.can_use_tool,
            )

        return self._capabilities

    async def disconnect(self) -> None:
        """Terminate the agent subprocess and clean up."""
        if self._query is not None:
            await self._query.close()
            self._query = None
        if self._connected:
            await self._rust_client.disconnect()
            self._connected = False

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

        text_str, content_json = self._prepare_prompt(text)
        await self._rust_client.send_prompt(text_str, session_id, content_json)
        while True:
            update = await self._rust_client.recv_update()
            if update is None:
                break
            yield update

    async def prompt_sync(
        self, text: str | list, *, session_id: str | None = None
    ) -> list[Message]:
        """Send a prompt and collect all response messages (non-streaming)."""
        return [msg async for msg in self.prompt(text, session_id=session_id)]

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
