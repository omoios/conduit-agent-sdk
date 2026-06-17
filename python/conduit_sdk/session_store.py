"""Session persistence for ACP sessions.

A :class:`SessionStore` durably records the ordered stream of
``session/update`` events for each session plus metadata, enabling replay,
dashboards, and cross-process sharing.

Backends:

- :class:`InMemorySessionStore` \u2014 process-local dict (default/tests).
- :class:`FileSessionStore` \u2014 one directory per session on disk (no deps).
- :class:`SqlSessionStore` \u2014 SQLAlchemy 2.0 async; one store for any dialect
  (``sqlite+aiosqlite:///:memory:`` for tests, ``postgresql+asyncpg://...``
  for production). Requires the ``sql`` extra.
- :class:`RedisSessionStore` \u2014 Redis lists/sets (``redis`` extra).

All backends implement the same async interface, so you can swap them without
changing application code.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
import urllib.parse
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

__all__ = [
    "SessionStore",
    "InMemorySessionStore",
    "FileSessionStore",
    "SqlSessionStore",
    "RedisSessionStore",
]


@runtime_checkable
class SessionStore(Protocol):
    """Async storage interface for ACP session events and metadata."""

    async def append_update(self, session_id: str, update: dict[str, Any]) -> None:
        """Append a serialized ``session/update`` event to a session's log."""
        ...

    async def load_updates(self, session_id: str) -> list[dict[str, Any]]:
        """Return a session's events in order (oldest first)."""
        ...

    async def set_metadata(self, session_id: str, metadata: dict[str, Any]) -> None:
        """Merge ``metadata`` fields into a session's stored metadata."""
        ...

    async def get_metadata(self, session_id: str) -> dict[str, Any] | None:
        """Return a session's metadata, or ``None`` if it does not exist."""
        ...

    async def list_sessions(self) -> list[str]:
        """Return the IDs of all known sessions."""
        ...

    async def delete_session(self, session_id: str) -> None:
        """Delete a session and all of its events."""
        ...

    async def close(self) -> None:
        """Release any backend resources."""
        ...


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# ---------------------------------------------------------------------------
# In-memory
# ---------------------------------------------------------------------------


class InMemorySessionStore:
    """Process-local session store backed by a dict. No dependencies."""

    def __init__(self) -> None:
        self._sessions: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def append_update(self, session_id: str, update: dict[str, Any]) -> None:
        async with self._lock:
            self._sessions.setdefault(
                session_id, {"metadata": {}, "events": [], "created_at": _now_iso()}
            )["events"].append(update)

    async def load_updates(self, session_id: str) -> list[dict[str, Any]]:
        async with self._lock:
            sess = self._sessions.get(session_id)
            return list(sess["events"]) if sess else []

    async def set_metadata(self, session_id: str, metadata: dict[str, Any]) -> None:
        async with self._lock:
            sess = self._sessions.setdefault(
                session_id, {"metadata": {}, "events": [], "created_at": _now_iso()}
            )
            sess["metadata"].update(metadata)
            sess["updated_at"] = _now_iso()

    async def get_metadata(self, session_id: str) -> dict[str, Any] | None:
        async with self._lock:
            return dict(self._sessions[session_id]["metadata"]) if session_id in self._sessions else None

    async def list_sessions(self) -> list[str]:
        async with self._lock:
            return list(self._sessions)

    async def delete_session(self, session_id: str) -> None:
        async with self._lock:
            self._sessions.pop(session_id, None)

    async def close(self) -> None:
        async with self._lock:
            self._sessions.clear()


# ---------------------------------------------------------------------------
# Filesystem
# ---------------------------------------------------------------------------


def _safe_name(session_id: str) -> str:
    return urllib.parse.quote(session_id, safe="")


class FileSessionStore:
    """Filesystem-backed store: ``<root>/<session>/metadata.json`` + ``events.jsonl``."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    def _session_dir(self, session_id: str) -> Path:
        return self._root / _safe_name(session_id)

    def _ensure(self, session_id: str) -> Path:
        d = self._session_dir(session_id)
        d.mkdir(parents=True, exist_ok=True)
        meta = d / "metadata.json"
        if not meta.exists():
            meta.write_text(json.dumps({"created_at": _now_iso()}))
        return d

    async def append_update(self, session_id: str, update: dict[str, Any]) -> None:
        def _write() -> None:
            d = self._ensure(session_id)
            with (d / "events.jsonl").open("a", encoding="utf-8") as f:
                f.write(json.dumps(update) + "\n")

        await asyncio.to_thread(_write)

    async def load_updates(self, session_id: str) -> list[dict[str, Any]]:
        def _read() -> list[dict[str, Any]]:
            path = self._session_dir(session_id) / "events.jsonl"
            if not path.exists():
                return []
            out: list[dict[str, Any]] = []
            with path.open(encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        out.append(json.loads(line))
            return out

        return await asyncio.to_thread(_read)

    async def set_metadata(self, session_id: str, metadata: dict[str, Any]) -> None:
        def _write() -> None:
            d = self._ensure(session_id)
            meta_path = d / "metadata.json"
            current = json.loads(meta_path.read_text()) if meta_path.exists() else {}
            current.update(metadata)
            current["updated_at"] = _now_iso()
            meta_path.write_text(json.dumps(current))

        await asyncio.to_thread(_write)

    async def get_metadata(self, session_id: str) -> dict[str, Any] | None:
        def _read() -> dict[str, Any] | None:
            meta_path = self._session_dir(session_id) / "metadata.json"
            if not meta_path.exists():
                return None
            return json.loads(meta_path.read_text())

        return await asyncio.to_thread(_read)

    async def list_sessions(self) -> list[str]:
        def _list() -> list[str]:
            if not self._root.exists():
                return []
            return [
                urllib.parse.unquote(p.name)
                for p in self._root.iterdir()
                if p.is_dir()
            ]

        return await asyncio.to_thread(_list)

    async def delete_session(self, session_id: str) -> None:
        d = self._session_dir(session_id)
        if d.exists():
            await asyncio.to_thread(shutil.rmtree, d)

    async def close(self) -> None:
        return None


# ---------------------------------------------------------------------------
# SQLAlchemy (SQLite / Postgres / any dialect)
# ---------------------------------------------------------------------------


class SqlSessionStore:
    """SQL-backed store using SQLAlchemy 2.0 async.

    Construct with any async engine, e.g.::

        # tests (in-memory SQLite)
        from sqlalchemy.ext.asyncio import create_async_engine
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        store = SqlSessionStore(engine)

        # production (Postgres)
        engine = create_async_engine("postgresql+asyncpg://user:pw@host/db")
        store = SqlSessionStore(engine)

    Requires the ``sql`` extra (``sqlalchemy`` + ``aiosqlite`` and/or ``asyncpg``).
    """

    def __init__(self, engine: Any) -> None:
        # Lazy import so the SDK does not hard-depend on SQLAlchemy.
        from sqlalchemy import (  # noqa: PLC0415
            BigInteger,
            Column,
            DateTime,
            ForeignKey,
            MetaData,
            String,
            Table,
            Text,
            func,
            Integer,
        )

        self._engine = engine
        self._metadata = MetaData()
        self._sessions = Table(
            "conduit_sessions",
            self._metadata,
            Column("id", String, primary_key=True),
            Column("metadata_json", Text, nullable=False, default="{}"),
            Column("created_at", DateTime, server_default=func.now()),
            Column("updated_at", DateTime, server_default=func.now()),
        )
        self._events = Table(
            "conduit_session_events",
            self._metadata,
            Column("id", BigInteger().with_variant(Integer, "sqlite"), primary_key=True),
            Column(
                "session_id",
                String,
                ForeignKey("conduit_sessions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            Column("event_json", Text, nullable=False),
            Column("created_at", DateTime, server_default=func.now()),
        )
        self._ready = False

    async def _ensure_schema(self) -> None:
        if self._ready:
            return
        async with self._engine.begin() as conn:
            await conn.run_sync(self._metadata.create_all)
        self._ready = True

    @staticmethod
    def _ensure_session_row_sync(session_id: str) -> tuple[Any, Any]:
        from sqlalchemy import insert, select  # noqa: PLC0415

        # Caller runs this inside run_sync with a live connection.
        return insert, select

    async def _ensure_session(self, conn: Any, session_id: str) -> None:
        from sqlalchemy import insert, select  # noqa: PLC0415

        existing = await conn.execute(
            self._sessions.select().where(self._sessions.c.id == session_id)
        )
        if existing.first() is None:
            await conn.execute(
                insert(self._sessions).values(id=session_id, metadata_json="{}")
            )

    async def append_update(self, session_id: str, update: dict[str, Any]) -> None:
        from sqlalchemy import insert  # noqa: PLC0415

        await self._ensure_schema()
        async with self._engine.begin() as conn:
            await self._ensure_session(conn, session_id)
            await conn.execute(
                insert(self._events).values(
                    session_id=session_id, event_json=json.dumps(update)
                )
            )

    async def load_updates(self, session_id: str) -> list[dict[str, Any]]:
        await self._ensure_schema()
        async with self._engine.connect() as conn:
            rows = await conn.execute(
                self._events.select()
                .with_only_columns(self._events.c.event_json)
                .where(self._events.c.session_id == session_id)
                .order_by(self._events.c.id)
            )
            return [json.loads(r[0]) for r in rows.fetchall()]

    async def set_metadata(self, session_id: str, metadata: dict[str, Any]) -> None:
        from sqlalchemy import update  # noqa: PLC0415

        await self._ensure_schema()
        async with self._engine.begin() as conn:
            await self._ensure_session(conn, session_id)
            existing = await conn.execute(
                self._sessions.select().where(self._sessions.c.id == session_id)
            )
            row = existing.first()
            current = json.loads(row[1]) if row and row[1] else {}
            current.update(metadata)
            await conn.execute(
                update(self._sessions)
                .where(self._sessions.c.id == session_id)
                .values(metadata_json=json.dumps(current), updated_at=func_now())
            )

    async def get_metadata(self, session_id: str) -> dict[str, Any] | None:
        await self._ensure_schema()
        async with self._engine.connect() as conn:
            rows = await conn.execute(
                self._sessions.select().where(self._sessions.c.id == session_id)
            )
            row = rows.first()
            if row is None:
                return None
            return json.loads(row[1]) if row[1] else {}

    async def list_sessions(self) -> list[str]:
        await self._ensure_schema()
        async with self._engine.connect() as conn:
            rows = await conn.execute(self._sessions.select())
            return [r[0] for r in rows.fetchall()]

    async def delete_session(self, session_id: str) -> None:
        from sqlalchemy import delete  # noqa: PLC0415

        await self._ensure_schema()
        async with self._engine.begin() as conn:
            await conn.execute(
                self._events.delete().where(self._events.c.session_id == session_id)
            )
            await conn.execute(
                self._sessions.delete().where(self._sessions.c.id == session_id)
            )

    async def close(self) -> None:
        await self._engine.dispose()


# Avoid a hard import of sqlalchemy.func at module import time.
def func_now() -> Any:
    from sqlalchemy import func  # noqa: PLC0415

    return func.now()


# ---------------------------------------------------------------------------
# Redis
# ---------------------------------------------------------------------------


class RedisSessionStore:
    """Redis-backed store using ``redis.asyncio``.

    Requires the ``redis`` extra. Construct with a Redis URL::

        store = RedisSessionStore("redis://localhost:6379/0")
    """

    def __init__(self, url: str) -> None:
        import redis.asyncio as aioredis  # noqa: PLC0415

        self._redis = aioredis.Redis.from_url(url)
        self._prefix = "conduit:session"

    def _meta_key(self, session_id: str) -> str:
        return f"{self._prefix}:{session_id}:meta"

    def _events_key(self, session_id: str) -> str:
        return f"{self._prefix}:{session_id}:events"

    async def append_update(self, session_id: str, update: dict[str, Any]) -> None:
        pipe = self._redis.pipeline()
        pipe.sadd(f"{self._prefix}:sessions", session_id)
        pipe.rpush(self._events_key(session_id), json.dumps(update))
        await pipe.execute()

    async def load_updates(self, session_id: str) -> list[dict[str, Any]]:
        raw = await self._redis.lrange(self._events_key(session_id), 0, -1)
        return [json.loads(item) for item in raw]

    async def set_metadata(self, session_id: str, metadata: dict[str, Any]) -> None:
        existing = await self._redis.get(self._meta_key(session_id))
        current = json.loads(existing) if existing else {}
        current.update(metadata)
        current["updated_at"] = _now_iso()
        pipe = self._redis.pipeline()
        pipe.sadd(f"{self._prefix}:sessions", session_id)
        pipe.set(self._meta_key(session_id), json.dumps(current))
        await pipe.execute()

    async def get_metadata(self, session_id: str) -> dict[str, Any] | None:
        raw = await self._redis.get(self._meta_key(session_id))
        return json.loads(raw) if raw else None

    async def list_sessions(self) -> list[str]:
        members = await self._redis.smembers(f"{self._prefix}:sessions")
        return [m.decode() if isinstance(m, bytes) else m for m in members]

    async def delete_session(self, session_id: str) -> None:
        pipe = self._redis.pipeline()
        pipe.delete(self._meta_key(session_id), self._events_key(session_id))
        pipe.srem(f"{self._prefix}:sessions", session_id)
        await pipe.execute()

    async def close(self) -> None:
        await self._redis.aclose()
