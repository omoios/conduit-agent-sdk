"""Postgres integration tests for SqlSessionStore.

Runs the **same** shared contract as the always-on SQLite suite, against a real
Postgres instance. Gated on the ``CONDUIT_PG_URL`` environment variable, which
must be a SQLAlchemy asyncpg URL, e.g.::

    CONDUIT_PG_URL="postgresql+asyncpg://user:pw@host:5432/db" \
        uv run pytest tests/test_session_store_postgres.py -q

The function-scoped ``store`` fixture drops the conduit tables before each test,
so the suite is order-independent (the contract uses fixed session IDs). SSL is
negotiated with ``prefer`` so the same fixture works for managed (TLS-required)
and local (plaintext) Postgres. Each test pays one TLS handshake against a
remote host — acceptable for an on-demand integration suite.
"""
from __future__ import annotations

import os

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine

from conduit_sdk.session_store import SqlSessionStore
from tests._session_store_contract import SessionStoreContract

PG_URL = os.environ.get("CONDUIT_PG_URL")

pytestmark = pytest.mark.skipif(
    not PG_URL,
    reason="set CONDUIT_PG_URL (SQLAlchemy asyncpg URL) to run Postgres integration tests",
)


async def _drop_tables(engine) -> None:
    # One statement per call — asyncpg rejects multi-statement batches.
    async with engine.begin() as conn:
        await conn.exec_driver_sql("DROP TABLE IF EXISTS conduit_session_events")
        await conn.exec_driver_sql("DROP TABLE IF EXISTS conduit_sessions")


@pytest_asyncio.fixture
async def store():
    engine = create_async_engine(PG_URL, connect_args={"ssl": "prefer"})
    await _drop_tables(engine)  # clean slate (recreated empty on first use)
    s = SqlSessionStore(engine)
    yield s
    await s.close()


class TestPostgresSessionStore(SessionStoreContract):
    """SqlSessionStore against real Postgres satisfies the shared contract."""


@pytest.mark.asyncio
async def test_postgres_event_id_column_is_bigint():
    """The BigInteger-with-sqlite-variant idiom must degrade to a real BIGINT
    (not INTEGER) on Postgres — guard against silent truncation at scale."""
    engine = create_async_engine(PG_URL, connect_args={"ssl": "prefer"})
    try:
        await _drop_tables(engine)
        s = SqlSessionStore(engine)
        await s.append_update(
            "s1",
            {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "x"}},
        )
        async with engine.connect() as conn:
            col_type = (
                await conn.exec_driver_sql(
                    "SELECT data_type FROM information_schema.columns "
                    "WHERE table_name = 'conduit_session_events' AND column_name = 'id'"
                )
            ).scalar()
        assert col_type == "bigint"
        await s.close()
    finally:
        await engine.dispose()
