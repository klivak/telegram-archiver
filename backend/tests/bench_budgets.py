"""Performance budgets from docs/17 (not part of the default test run).

    uv run python tests/bench_budgets.py [messages] [chats]
"""

from __future__ import annotations

import asyncio
import random
import sys
import tempfile
import time
from pathlib import Path

from fastapi.testclient import TestClient

from tgarchiver.api.app import create_app
from tgarchiver.core.config import Settings
from tgarchiver.core.db import Database
from tgarchiver.core.secrets import MemorySecretStore
from tgarchiver.services import Services
from tgarchiver.sync.search import search

WORDS = ("привіт бюджет звіт зустріч проєкт дедлайн реліз архів телеграм кава понеділок hello budget report meeting "
         "project deadline release archive coffee monday invoice design review sprint ticket").split()


async def seed(db_path: Path, n_msgs: int, n_chats: int) -> None:
    db = Database(db_path)
    await db.open()
    rnd = random.Random(1)
    async with db.tx() as c:
        await c.executemany(
            "INSERT INTO chats(id, type, title, unread_count, last_message_at, stored_messages) VALUES(?,?,?,?,?,?)",
            [(i, rnd.choice(["user", "group", "channel", "supergroup"]), f"Chat {i} {rnd.choice(WORDS)}",
              rnd.randint(0, 5), f"2026-0{rnd.randint(1, 9)}-1{rnd.randint(0, 9)}T10:00:00", 0)
             for i in range(1, n_chats + 1)])
    batch = []
    for i in range(1, n_msgs + 1):
        text = " ".join(rnd.choice(WORDS) for _ in range(rnd.randint(3, 20)))
        batch.append((rnd.randint(1, min(n_chats, 200)), i, f"2026-0{rnd.randint(1, 9)}-{rnd.randint(10, 28)}T10:00:00",
                      text))
        if len(batch) == 20000:
            async with db.tx() as c:
                await c.executemany("INSERT INTO messages(chat_id, id, date, text) VALUES(?,?,?,?)", batch)
            batch = []
    if batch:
        async with db.tx() as c:
            await c.executemany("INSERT INTO messages(chat_id, id, date, text) VALUES(?,?,?,?)", batch)
    await db.close()


async def bench_search(db_path: Path) -> None:
    db = Database(db_path)
    await db.open()
    for q in ("бюджет", "звіт дедлайн", '"hello budget"', "проє"):
        await search(db, q)  # warm
        t0 = time.perf_counter()
        r = await search(db, q)
        dt = (time.perf_counter() - t0) * 1000
        print(f"search {q!r:>18}: {dt:7.1f} ms  total={r['total']}  {'OK' if dt < 200 else 'OVER BUDGET'}")
    await db.close()


def bench_chat_list(root: Path) -> None:
    svc = Services(Settings(archive_root=str(root)), MemorySecretStore(), persist_settings=False)
    with TestClient(create_app(svc, "t")) as c:
        c.headers["Authorization"] = "Bearer t"
        c.get("/api/chats")
        t0 = time.perf_counter()
        n = len(c.get("/api/chats").json()["items"])
        dt = (time.perf_counter() - t0) * 1000
        print(f"chat list ({n} chats): {dt:.1f} ms  {'OK' if dt < 300 else 'OVER BUDGET'}")


def main() -> None:
    n_msgs = int(sys.argv[1]) if len(sys.argv) > 1 else 1_000_000
    n_chats = int(sys.argv[2]) if len(sys.argv) > 2 else 2000
    root = Path(tempfile.mkdtemp())
    t0 = time.perf_counter()
    asyncio.run(seed(root / "tgarchiver.db", n_msgs, n_chats))
    print(f"seeded {n_msgs:,} messages / {n_chats} chats in {time.perf_counter() - t0:.1f}s")
    asyncio.run(bench_search(root / "tgarchiver.db"))
    bench_chat_list(root)


if __name__ == "__main__":
    main()
