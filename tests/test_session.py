"""Tests for conduit_sdk.Session."""

from __future__ import annotations

import pytest

from conduit_sdk import Client, Session
from conduit_sdk.exceptions import SessionError


class TestSessionInit:
    def test_initial_state(self):
        client = Client(["echo"])
        session = Session(client)
        assert session.session_id is None
        assert session.mode is None
        assert session.model is None

    def test_repr(self):
        client = Client(["echo"])
        session = Session(client)
        assert "Session" in repr(session)


class TestSessionGuards:
    @pytest.mark.asyncio
    async def test_fork_without_create_raises(self):
        client = Client(["echo"])
        session = Session(client)
        with pytest.raises(SessionError, match="hasn't been created"):
            await session.fork()

    @pytest.mark.asyncio
    async def test_set_mode_without_create_raises(self):
        client = Client(["echo"])
        session = Session(client)
        with pytest.raises(SessionError, match="not created"):
            await session.set_mode("code")

    @pytest.mark.asyncio
    async def test_set_model_without_create_raises(self):
        client = Client(["echo"])
        session = Session(client)
        with pytest.raises(SessionError, match="not created"):
            await session.set_model("claude-4")
