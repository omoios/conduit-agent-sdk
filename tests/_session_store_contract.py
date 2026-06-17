"""Shared SessionStore contract exercised across every backend.

The same assertions run against InMemory / File / SQLite (always on) and
Postgres / Redis (integration, env-gated), guaranteeing identical behavior
from each backend.

To use it, subclass :class:`SessionStoreContract` in a test module and define
a module-level async ``store`` fixture that yields an initialized store and
closes it on teardown::

    class TestMyBackend(SessionStoreContract):
        pass

    @pytest_asyncio.fixture
    async def store():
        s = MyStore(...)
        yield s
        await s.close()
"""

from __future__ import annotations

import pytest


def _chunk(text: str) -> dict:
    """A minimal session-update payload, as stored by append_update."""
    return {
        "sessionUpdate": "agent_message_chunk",
        "content": {"type": "text", "text": text},
    }


class SessionStoreContract:
    """Backend-agnostic async contract for any :class:`SessionStore`.

    Pytest collects the inherited ``test_*`` methods on subclasses (which must
    be named ``Test*``); fixtures resolve from the subclass's own module.
    """

    @pytest.mark.asyncio
    async def test_append_load_preserves_order(self, store):
        await store.append_update("s1", _chunk("a"))
        await store.append_update("s1", _chunk("b"))
        await store.append_update("s1", _chunk("c"))
        out = await store.load_updates("s1")
        assert [u["content"]["text"] for u in out] == ["a", "b", "c"]

    @pytest.mark.asyncio
    async def test_load_unknown_session_is_empty(self, store):
        assert await store.load_updates("nope") == []

    @pytest.mark.asyncio
    async def test_metadata_set_get_and_merge(self, store):
        await store.set_metadata("s1", {"title": "T", "cwd": "/tmp"})
        meta = await store.get_metadata("s1")
        assert meta is not None
        assert meta["title"] == "T"

        # Second set merges rather than replacing.
        await store.set_metadata("s1", {"model": "gpt"})
        meta = await store.get_metadata("s1")
        assert meta["title"] == "T"
        assert meta["model"] == "gpt"

    @pytest.mark.asyncio
    async def test_get_metadata_missing_is_none(self, store):
        assert await store.get_metadata("missing") is None

    @pytest.mark.asyncio
    async def test_append_creates_session_for_listing(self, store):
        await store.append_update("a", _chunk("x"))
        await store.append_update("b", _chunk("y"))
        sessions = set(await store.list_sessions())
        assert {"a", "b"}.issubset(sessions)

    @pytest.mark.asyncio
    async def test_delete_removes_events_and_metadata(self, store):
        await store.append_update("s1", _chunk("a"))
        await store.set_metadata("s1", {"title": "T"})
        await store.delete_session("s1")
        assert await store.load_updates("s1") == []
        assert await store.get_metadata("s1") is None

    @pytest.mark.asyncio
    async def test_delete_is_idempotent(self, store):
        await store.delete_session("never-existed")
        assert await store.list_sessions() == []

    @pytest.mark.asyncio
    async def test_isolated_sessions(self, store):
        await store.append_update("s1", _chunk("one"))
        await store.append_update("s2", _chunk("two"))
        assert len(await store.load_updates("s1")) == 1
        assert len(await store.load_updates("s2")) == 1
        assert (await store.load_updates("s1"))[0]["content"]["text"] == "one"
