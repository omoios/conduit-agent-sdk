"""Session management for conduit-agent-sdk.

Sessions represent independent conversation threads with an agent.
Each session has its own message history, mode, and model settings.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from conduit_sdk.exceptions import SessionError

if TYPE_CHECKING:
    from conduit_sdk.client import Client
    from conduit_sdk.types import Message


class Session:
    """An ACP conversation session.

    Sessions are created via :meth:`Client.new_session` or
    :meth:`Session.load`.

    Parameters
    ----------
    client:
        The parent :class:`Client` that owns this session.
    """

    def __init__(self, client: Client) -> None:
        self._client = client
        self._session_id: str | None = None
        self._mode: str | None = None

    # -- Lifecycle -----------------------------------------------------------

    async def create(
        self,
        cwd: str | None = None,
        *,
        meta_json: str | None = None,
        mcp_servers_json: str | None = None,
    ) -> str:
        """Create a new ACP session and return its ID."""
        self._session_id = await self._client._rust_client.new_session(
            cwd, meta_json, mcp_servers_json
        )
        return self._session_id

    async def load(self, session_id: str, cwd: str | None = None) -> str:
        """Resume an existing session by ID."""
        self._session_id = await self._client._rust_client.load_session(
            session_id, cwd
        )
        return self._session_id

    # -- Configuration -------------------------------------------------------

    async def set_mode(self, mode: str) -> None:
        """Set the agent mode (e.g. ``"ask"``, ``"code"``, ``"architect"``)."""
        if self._session_id is None:
            raise SessionError("session not created")
        await self._client._rust_client.set_session_mode(self._session_id, mode)
        self._mode = mode

    async def set_config(self, config_id: str, value: str) -> dict:
        """Set a configuration option on this session."""
        if self._session_id is None:
            raise SessionError("session not created")
        return await self._client.set_config(self._session_id, config_id, value)

    async def cancel(self) -> None:
        """Cancel/interrupt the current operation in this session."""
        if self._session_id is None:
            raise SessionError("session not created")
        await self._client.cancel(self._session_id)

    async def fork(self, cwd: str | None = None) -> Session:
        """Fork this session into a new one with shared history."""
        if self._session_id is None:
            raise SessionError("session not created")
        return await self._client.fork_session(self._session_id, cwd)

    # -- Prompting -----------------------------------------------------------

    async def prompt(self, text: str) -> list[Message]:
        """Send a prompt within this session."""
        if self._session_id is None:
            raise SessionError("session not created — call create() first")
        return await self._client.prompt_sync(text, session_id=self._session_id)

    # -- Properties ----------------------------------------------------------

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def mode(self) -> str | None:
        return self._mode

    def __repr__(self) -> str:
        return f"Session(id={self._session_id!r}, mode={self._mode!r})"
