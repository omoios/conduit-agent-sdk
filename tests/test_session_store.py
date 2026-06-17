"""Tests for conduit_sdk.session_store backends.

Each backend is exercised through the same async contract via a parametrized
fixture, so InMemory / File / Sql(SQLite) are guaranteed to behave identically.
The Sql backend runs against an in-memory SQLite (aiosqlite) database.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import StaticPool

from conduit_sdk.session_store import (
    FileSessionStore,
    InMemorySessionStore,
    SqlSessionStore,
)

BACKENDS = ["inmemory", "file", "sqlite"]


@pytest_asyncio.fixture(params=BACKENDS)
async def store(request, tmp_path):
    if request.param == "inmemory":
        s = InMemorySessionStore()
    elif request.param == "file":
        s = FileSessionStore(tmp_path / "sessions")
    else:
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        s = SqlSessionStore(engine)
    yield s
    await s.close()


def _chunk(text: str) -> dict:
    return {
        "sessionUpdate": "agent_message_chunk",
        "content": {"type": "text", "text": text},
    }


@pytest.mark.asyncio
async def test_append_load_preserves_order(store):
    await store.append_update("s1", _chunk("a"))
    await store.append_update("s1", _chunk("b"))
    await store.append_update("s1", _chunk("c"))
    out = await store.load_updates("s1")
    assert [u["content"]["text"] for u in out] == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_load_unknown_session_is_empty(store):
    assert await store.load_updates("nope") == []


@pytest.mark.asyncio
async def test_metadata_set_get_and_merge(store):
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
async def test_get_metadata_missing_is_none(store):
    assert await store.get_metadata("missing") is None


@pytest.mark.asyncio
async def test_append_creates_session_for_listing(store):
    await store.append_update("a", _chunk("x"))
    await store.append_update("b", _chunk("y"))
    sessions = set(await store.list_sessions())
    assert {"a", "b"}.issubset(sessions)


@pytest.mark.asyncio
async def test_delete_removes_events_and_metadata(store):
    await store.append_update("s1", _chunk("a"))
    await store.set_metadata("s1", {"title": "T"})
    await store.delete_session("s1")
    assert await store.load_updates("s1") == []
    assert await store.get_metadata("s1") is None


@pytest.mark.asyncio
async def test_delete_is_idempotent(store):
    await store.delete_session("never-existed")
    assert await store.list_sessions() == []


@pytest.mark.asyncio
async def test_isolated_sessions(store):
    await store.append_update("s1", _chunk("one"))
    await store.append_update("s2", _chunk("two"))
    assert len(await store.load_updates("s1")) == 1
    assert len(await store.load_updates("s2")) == 1
    assert (await store.load_updates("s1"))[0]["content"]["text"] == "one"


# --- SqlSessionStore: dispose/cleanup --------------------------------------


@pytest.mark.asyncio
async def test_sql_store_session_protocol_field_safety(tmp_path):
    # Session IDs with filesystem/SQL-unfriendly characters must round-trip
    # via the Sql backend (ids are string PKs, not filenames).
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    s = SqlSessionStore(engine)
    weird = "s/../weird:id with spaces"
    await s.append_update(weird, _chunk("ok"))
    assert len(await s.load_updates(weird)) == 1
    assert weird in await s.list_sessions()
    await s.close()
