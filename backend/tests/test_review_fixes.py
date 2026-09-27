"""Regression tests for issues found in code review."""

from __future__ import annotations

import asyncio
from pathlib import Path

from conftest import FakeClient, add_chat, make_doc, make_msg, use_client
from fastapi.testclient import TestClient

from tgarchiver.api.app import create_app
from tgarchiver.core.config import Settings
from tgarchiver.core.secrets import MemorySecretStore
from tgarchiver.export.exporter import Exporter
from tgarchiver.export.writers import format_entities
from tgarchiver.media.downloader import REQUEST_SIZE, enqueue, media_path
from tgarchiver.miniapps.recorder import is_safe
from tgarchiver.services import Services
from tgarchiver.sync.history import sync_chat_history


def test_unsafe_link_schemes_are_not_rendered() -> None:
    out = format_entities("click me", [{"t": "TextUrl", "o": 0, "l": 5, "url": "javascript:alert(1)"}], "html")
    assert "javascript" not in out and "<a" not in out
    out = format_entities("javascript://x%0aalert(1)", [{"t": "Url", "o": 0, "l": 25}], "html")
    assert 'href="https://javascript' in out


def test_crawler_checks_enclosing_clickable() -> None:
    assert not is_safe({"text": "5", "ctx": "Pay 5 ⭐", "href": ""})
    assert not is_safe({"text": "OK", "href": ""})
    assert not is_safe({"text": "Так", "href": ""})


async def test_export_never_overwrites_part_when_period_repeats(svc: Services) -> None:
    await add_chat(svc)
    rows = [(2000, 1, "2026-02-01T10:00:00", "feb"), (2000, 2, "2026-03-01T10:00:00", "mar"),
            (2000, 3, "2026-02-15T10:00:00", "imported feb")]  # newer id, older date
    await svc.db.executemany("INSERT INTO messages(chat_id, id, date, text) VALUES(?,?,?,?)", rows)
    chat = await svc.db.fetchone("SELECT * FROM chats WHERE id=2000")
    res = await Exporter(svc, chat, "md", {"mode": "month"}).run()
    assert res["parts"] == 3 and res["messages"] == 3
    out = svc.account_dir / "chats" / "Alice_2000" / "export" / "md"
    text = "".join(p.read_text(encoding="utf-8") for p in out.rglob("2026-*.md"))
    assert "feb" in text and "imported feb" in text and "mar" in text


async def test_pause_before_launch_wins(svc: Services) -> None:
    await svc.engine.stop()  # no loop: simulate the race deterministically
    jid = await svc.engine.submit("demo", {"steps": 1, "delay": 0})
    job = await svc.db.fetchone("SELECT * FROM jobs WHERE id=?", (jid,))
    await svc.engine.pause(jid)
    await svc.engine._launch(job)  # a stale _tick read
    assert jid not in svc.engine.running
    assert (await svc.engine.get(jid))["status"] == "paused"  # type: ignore[index]
    await svc.engine.start()


async def test_kv_add_is_atomic(svc: Services) -> None:
    await asyncio.gather(*(svc.db.kv_add("bytes_x", 10) for _ in range(50)))
    assert await svc.db.kv_get("bytes_x") == 500


async def test_stale_part_is_discarded(svc: Services) -> None:
    await add_chat(svc)
    data = b"n" * (REQUEST_SIZE + 10)
    use_client(svc, FakeClient([make_msg(1, media=make_doc(41, len(data), name="f.bin"))], {41: data}))
    await sync_chat_history(svc, _Ctx(), 2000)  # type: ignore[arg-type]
    row = await svc.db.fetchone("SELECT * FROM media")
    chat = await svc.db.fetchone("SELECT * FROM chats WHERE id=2000")
    final = media_path(svc, chat, row)
    final.parent.mkdir(parents=True, exist_ok=True)
    final.with_name(final.name + ".part").write_bytes(b"OLD" * REQUEST_SIZE)  # other file, no marker
    await enqueue(svc, [2000], ["document"], {})
    job = await svc.engine.wait(await svc.engine.submit("download_media", {}), timeout=30)
    assert job["status"] == "done"
    assert Path((await svc.db.fetchone("SELECT path FROM media"))["path"]).read_bytes() == data


class _Ctx:
    progress_data: dict = {}
    _pause = _cancel = False

    async def check(self) -> None: ...

    async def progress(self, **kw) -> None: ...


def test_untrusted_files_are_sandboxed(tmp_path: Path) -> None:
    root = tmp_path / "a"
    (root / "x").mkdir(parents=True)
    evil = root / "x" / "evil.html"
    evil.write_text("<script>fetch('/')</script>", encoding="utf-8")
    svc = Services(Settings(archive_root=str(root)), MemorySecretStore(), persist_settings=False)
    with TestClient(create_app(svc, "t")) as c:
        r = c.get("/api/archive-file", params={"path": str(evil), "token": "t"})
        assert r.status_code == 200
        assert r.headers["content-security-policy"] == "sandbox"
