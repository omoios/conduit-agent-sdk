"""Tests for conduit_sdk.session_store backends.

Each always-on backend (InMemory / File / Sql(SQLite)) is exercised through the
shared async contract via a parametrized fixture, guaranteeing identical
behavior. The Sql backend runs against an in-memory SQLite (aiosqlite) DB.

The shared contract lives in :mod:`tests._session_store_contract` and is also
run against Postgres and Redis — env-gated integration suites in
``test_session_store_postgres`` and ``test_session_store_redis``.
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
from tests._session_store_contract import SessionStoreContract, _chunk

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


class TestCoreBackends(SessionStoreContract):
    """In-memory, File, and SQLite all satisfy the shared SessionStore contract."""


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
