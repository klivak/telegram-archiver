"""SQLite access (aiosqlite, WAL) with versioned migrations."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import AsyncIterator, Iterable, Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiosqlite

from tgarchiver.core.migrations import MIGRATIONS


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def dumps(v: Any) -> str | None:
    return None if v is None else json.dumps(v, ensure_ascii=False, default=str)


def loads(v: str | None, default: Any = None) -> Any:
    if v is None or v == "":
        return default
    if not isinstance(v, (str, bytes)):
        return v  # already decoded
    try:
        return json.loads(v)
    except ValueError:
        return default


class Database:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        self.conn: aiosqlite.Connection | None = None
        self.rconn: aiosqlite.Connection | None = None  # separate reader for long streaming cursors (exports)
        self._write_lock = asyncio.Lock()

    async def open(self) -> None:
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = await aiosqlite.connect(self.path, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        for pragma in (
            "PRAGMA journal_mode=WAL",
            "PRAGMA synchronous=NORMAL",
            "PRAGMA foreign_keys=ON",
            "PRAGMA temp_store=MEMORY",
            "PRAGMA cache_size=-32000",
            "PRAGMA busy_timeout=5000",
        ):
            await self.conn.execute(pragma)
        await self.migrate()
        if self.path != ":memory:":
            self.rconn = await aiosqlite.connect(self.path, isolation_level=None)
            self.rconn.row_factory = sqlite3.Row
            await self.rconn.execute("PRAGMA busy_timeout=5000")

    async def close(self) -> None:
        if self.rconn is not None:
            await self.rconn.close()
            self.rconn = None
        if self.conn is not None:
            await self.conn.close()
            self.conn = None

    @property
    def c(self) -> aiosqlite.Connection:
        assert self.conn is not None, "database is not open"
        return self.conn

    async def migrate(self) -> int:
        row = await (await self.c.execute("PRAGMA user_version")).fetchone()
        version = int(row[0]) if row else 0
        for i, sql in enumerate(MIGRATIONS[version:], start=version + 1):
            async with self._write_lock:
                await self.c.execute("BEGIN")
                try:
                    for stmt in _split_sql(sql):
                        await self.c.execute(stmt)
                    await self.c.execute(f"PRAGMA user_version={i}")
                    await self.c.execute("COMMIT")
                except BaseException:
                    await self.c.execute("ROLLBACK")
                    raise
        return len(MIGRATIONS)

    @asynccontextmanager
    async def tx(self) -> AsyncIterator[aiosqlite.Connection]:
        """Serialized write transaction (batch inserts go here)."""
        async with self._write_lock:
            await self.c.execute("BEGIN")
            try:
                yield self.c
            except BaseException:
                await self.c.execute("ROLLBACK")
                raise
            else:
                await self.c.execute("COMMIT")

    async def execute(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> int:
        """Returns lastrowid for INSERT, affected row count otherwise. Not re-entrant: never call inside tx()."""
        async with self._write_lock:
            cur = await self.c.execute(sql, params)
            if sql.lstrip()[:6].upper() == "INSERT":
                return int(cur.lastrowid or 0)
            return int(cur.rowcount)

    async def executemany(self, sql: str, rows: Iterable[Sequence[Any] | dict[str, Any]]) -> None:
        async with self.tx() as c:
            await c.executemany(sql, rows)

    async def fetchall(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> list[dict[str, Any]]:
        cur = await self.c.execute(sql, params)
        rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def fetchone(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> dict[str, Any] | None:
        cur = await self.c.execute(sql, params)
        row = await cur.fetchone()
        return dict(row) if row else None

    async def scalar(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> Any:
        cur = await self.c.execute(sql, params)
        row = await cur.fetchone()
        return row[0] if row else None

    async def iterate(
        self, sql: str, params: Sequence[Any] | dict[str, Any] = (), batch: int = 500
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """Stream rows in batches (exports never load a whole chat into memory).

        Uses the reader connection (WAL snapshot), so a writer's ROLLBACK can't abort the cursor mid-export.
        """
        cur = await (self.rconn or self.c).execute(sql, params)
        while True:
            rows = await cur.fetchmany(batch)
            if not rows:
                break
            yield [dict(r) for r in rows]

    async def kv_get(self, key: str, default: Any = None) -> Any:
        return loads(await self.scalar("SELECT value FROM kv WHERE key=?", (key,)), default)

    async def kv_add(self, key: str, delta: int) -> None:
        """Atomic counter increment (concurrent downloads)."""
        await self.execute(
            "INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=value+excluded.value",
            (key, int(delta)),
        )

    async def kv_set(self, key: str, value: Any) -> None:
        await self.execute(
            "INSERT INTO kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, dumps(value)),
        )


def _split_sql(sql: str) -> list[str]:
    """Split a migration script into statements, keeping trigger bodies intact."""
    out: list[str] = []
    buf: list[str] = []
    for line in sql.strip().splitlines():
        buf.append(line)
        chunk = "\n".join(buf).strip()
        if chunk.endswith(";") and sqlite3.complete_statement(chunk):
            out.append(chunk)
            buf = []
    tail = "\n".join(buf).strip()
    if tail:
        out.append(tail)
    return out
