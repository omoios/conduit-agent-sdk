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

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from conduit_sdk._conduit_sdk import ClientConfig, RustClient, RustControlProtocol
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

    # -- Prompting -----------------------------------------------------------

    async def prompt(self, text: str) -> AsyncIterator[Message]:
        """Send a prompt to the agent and stream back response messages.

        Yields :class:`Message` objects as they arrive from the agent.
        """
        if not self._connected:
            raise ConnectionError("client is not connected — call connect() first")

        # TODO: Replace with true streaming once the Rust transport
        # emits SessionUpdate notifications incrementally.
        messages = await self._rust_client.prompt(text)
        for msg in messages:
            yield msg

    async def prompt_sync(self, text: str) -> list[Message]:
        """Send a prompt and collect all response messages (non-streaming)."""
        return [msg async for msg in self.prompt(text)]

    # -- Control protocol methods -------------------------------------------

    async def interrupt(self) -> None:
        """Send an interrupt to stop the agent's current operation."""
        if self._query is not None:
            await self._query.interrupt()

    async def set_permission_mode(self, mode: str) -> None:
        """Change the permission mode mid-session."""
        if self._query is not None:
            await self._query.set_permission_mode(mode)

    async def set_model(self, model: str) -> None:
        """Change the model mid-session."""
        if self._query is not None:
            await self._query.set_model(model)

    # -- Session shortcuts ---------------------------------------------------

    async def new_session(self) -> Session:
        """Create a new conversation session on this client."""
        session = Session(self)
        await session.create()
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
