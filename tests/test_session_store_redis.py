"""Redis integration tests for RedisSessionStore.

Runs the **same** shared contract as the always-on suites, against a real Redis
instance. Gated on the ``CONDUIT_REDIS_URL`` environment variable, e.g.::

    CONDUIT_REDIS_URL="redis://localhost:6379/0" \
        uv run pytest tests/test_session_store_redis.py -q

The fixture flushes the DB before each test so the suite is order-independent
(the contract uses fixed session IDs).
"""
from __future__ import annotations

import os

import pytest
import pytest_asyncio

from conduit_sdk.session_store import RedisSessionStore
from tests._session_store_contract import SessionStoreContract

REDIS_URL = os.environ.get("CONDUIT_REDIS_URL")

pytestmark = pytest.mark.skipif(
    not REDIS_URL,
    reason="set CONDUIT_REDIS_URL to run Redis integration tests",
)


@pytest_asyncio.fixture
async def store():
    s = RedisSessionStore(REDIS_URL)
    # Clean slate per test — the contract reuses fixed session IDs and asserts
    # exact contents, so leftover keys would corrupt results.
    await s._redis.flushdb()
    yield s
    await s._redis.flushdb()
    await s.close()


class TestRedisSessionStore(SessionStoreContract):
    """RedisSessionStore against real Redis satisfies the shared contract."""
