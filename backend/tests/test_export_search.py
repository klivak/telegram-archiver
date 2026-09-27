from __future__ import annotations

import json

from conftest import add_chat

from tgarchiver.export.exporter import Exporter
from tgarchiver.export.writers import format_entities, render_to_string
from tgarchiver.services import Services
from tgarchiver.sync.search import fts_query, search


async def _seed(svc: Services, n: int = 120, chat_id: int = 2000) -> None:
    await add_chat(svc, chat_id=chat_id, title="Робочий чат")
    await svc.db.execute("INSERT OR IGNORE INTO users(id, first_name) VALUES(2000, 'Alice')")
    rows = []
    for i in range(1, n + 1):
        month = 1 + (i - 1) * 3 // n  # spread over 3 months
        rows.append((chat_id, i, 2000, f"2026-{month:02d}-{(i % 27) + 1:02d}T10:00:00",
                     f"повідомлення {i} про бюджет" if i % 10 == 0 else f"message {i} https://x.dev", 0, 1))
    await svc.db.executemany(
        "INSERT INTO messages(chat_id, id, sender_id, date, text, out, has_link) VALUES(?,?,?,?,?,?,?)", rows)


def test_entities_utf16() -> None:
    text = "😀 bold link"
    ents = [{"t": "Bold", "o": 3, "l": 4}, {"t": "TextUrl", "o": 8, "l": 4, "url": "https://a.b"}]
    assert format_entities(text, ents, "html") == '😀 <b>bold</b> <a href="https://a.b" rel="noopener noreferrer">link</a>'
    assert format_entities(text, ents, "md") == "😀 **bold** [link](https://a.b)"
    assert format_entities("<x>", None, "html") == "&lt;x&gt;"


def test_writers_all_formats() -> None:
    meta = {"chat_id": 1, "chat_title": "T", "chat_type": "user", "noforwards": True, "part_no": 1, "part_label": "x"}
    msgs = [{"id": 1, "date": "2026-01-01T10:00:00", "text": "hi", "out": 0, "first_name": "A", "raw": {},
             "sender_id": 5}]
    j = json.loads(render_to_string("json", meta, msgs))
    assert j["messages"][0]["text"] == "hi" and "notice" in j
    assert "Protected content" in render_to_string("md", meta, msgs)
    assert render_to_string("csv", meta, msgs).startswith("﻿id,date")
    assert json.loads(render_to_string("jsonl", meta, msgs).strip())["from"] == "A"
    assert "[2026-01-01 10:00] A: hi" in render_to_string("txt", meta, msgs)


async def test_export_splits_and_incremental(svc: Services) -> None:
    await _seed(svc)
    chat = await svc.db.fetchone("SELECT * FROM chats WHERE id=2000")
    res = await Exporter(svc, chat, "md", {"mode": "month"}).run()
    assert res["parts"] == 3 and res["messages"] == 120
    idx = json.loads((svc.account_dir / "chats" / "Робочий_чат_2000" / "export" / "md" / "index.json")
                     .read_text(encoding="utf-8"))
    first_part_file = idx["parts"][0]["file"]
    first_mtime = (svc.account_dir / "chats" / "Робочий_чат_2000" / "export" / "md" / first_part_file).stat().st_mtime_ns
    # new messages: only the last part is rewritten
    await svc.db.execute("INSERT INTO messages(chat_id, id, sender_id, date, text) VALUES(2000, 121, 2000, "
                         "'2026-03-28T10:00:00', 'fresh')")
    res2 = await Exporter(svc, chat, "md", {"mode": "month"}).run()
    assert res2["messages"] == 121 and res2["parts"] == 3
    p1 = svc.account_dir / "chats" / "Робочий_чат_2000" / "export" / "md" / first_part_file
    assert p1.stat().st_mtime_ns == first_mtime
    # size split
    res3 = await Exporter(svc, chat, "txt", {"mode": "size", "size_mb": 0.002}).run()
    assert res3["parts"] > 3
    # llm split with overlap
    res4 = await Exporter(svc, chat, "md", {"mode": "llm", "tokens": 300, "overlap": 2}).run()
    assert res4["parts"] > 1
    # single + html
    res5 = await Exporter(svc, chat, "html", {"mode": "single"}).run()
    assert res5["parts"] == 1


async def test_export_pause_releases_file_and_resumes(svc: Services) -> None:
    from tgarchiver.jobs.engine import JobPaused

    await _seed(svc, n=1200)
    chat = await svc.db.fetchone("SELECT * FROM chats WHERE id=2000")
    ex = Exporter(svc, chat, "md", {"mode": "single"})

    class Ctx:
        calls = 0

        async def check(self) -> None:
            self.calls += 1
            if self.calls == 2:
                raise JobPaused()

        async def progress(self, **_: object) -> None:
            pass

    try:
        await ex.run(Ctx())  # type: ignore[arg-type]
        raise AssertionError("expected pause")
    except JobPaused:
        pass
    part = ex.out_dir / "messages.md"
    part.unlink()  # would fail on Windows if the handle leaked
    res = await Exporter(svc, chat, "md", {"mode": "single"}).run()
    assert res["messages"] == 1200


async def test_search_filters(svc: Services) -> None:
    await _seed(svc)
    r = await search(svc.db, "бюдж")
    assert r["total"] == 12 and "<mark>" in r["items"][0]["snippet"]
    r = await search(svc.db, "message", {"date_from": "2026-03-01"})
    assert all(it["date"] >= "2026-03-01" for it in r["items"])
    r = await search(svc.db, "", {"has_link": True, "chat_ids": [2000]}, limit=5)
    assert len(r["items"]) == 5
    assert (await search(svc.db, "nothing-like-this"))["total"] == 0
    assert fts_query('a "b c" d-e') == '"a"* AND "b c" AND "d"* AND "e"*'
    assert fts_query('") OR *') == '"OR"*'  # operators become quoted literals
