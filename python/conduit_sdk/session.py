"""Session management for conduit-agent-sdk.

Sessions represent independent conversation threads with an agent.
Each session has its own message history, mode, and model settings.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from conduit_sdk._conduit_sdk import RustSessionManager
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
        self._manager = RustSessionManager()
        self._session_id: str | None = None
        self._mode: str | None = None
        self._model: str | None = None

    # -- Lifecycle -----------------------------------------------------------

    async def create(self) -> str:
        """Create a new session and return its ID."""
        self._session_id = await self._manager.create()
        return self._session_id

    async def load(self, session_id: str) -> str:
        """Resume an existing session by ID."""
        self._session_id = await self._manager.load(session_id)
        return self._session_id

    async def fork(self) -> Session:
        """Fork this session into a new independent session.

        The new session starts with the same conversation history.
        """
        if self._session_id is None:
            raise SessionError("cannot fork a session that hasn't been created")

        new_session = Session(self._client)
        new_id = await self._manager.fork(self._session_id)
        new_session._session_id = new_id
        new_session._mode = self._mode
        new_session._model = self._model
        return new_session

    # -- Configuration -------------------------------------------------------

    async def set_mode(self, mode: str) -> None:
        """Set the agent mode (e.g. ``"ask"``, ``"code"``, ``"architect"``)."""
        if self._session_id is None:
            raise SessionError("session not created")
        await self._manager.set_mode(self._session_id, mode)
        self._mode = mode

    async def set_model(self, model: str) -> None:
        """Set the model for this session."""
        if self._session_id is None:
            raise SessionError("session not created")
        await self._manager.set_model(self._session_id, model)
        self._model = model

    # -- Prompting -----------------------------------------------------------

    async def prompt(self, text: str) -> list[Message]:
        """Send a prompt within this session."""
        # Delegate to the client, which tags messages with our session ID.
        return await self._client.prompt_sync(text)

    # -- Properties ----------------------------------------------------------

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def mode(self) -> str | None:
        return self._mode

    @property
    def model(self) -> str | None:
        return self._model

    def __repr__(self) -> str:
        return f"Session(id={self._session_id!r}, mode={self._mode!r})"
