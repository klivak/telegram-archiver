from __future__ import annotations

import hashlib
from pathlib import Path

from conftest import FakeClient, add_chat, make_doc, make_msg, use_client
from telethon.tl import types

from tgarchiver.jobs.engine import FloodWait
from tgarchiver.media.downloader import REQUEST_SIZE, enqueue, estimate
from tgarchiver.services import Services
from tgarchiver.sync.history import sync_chat_history
from tgarchiver.sync.normalize import classify_media, find_mini_apps, message_row


def test_classify_media() -> None:
    voice = make_msg(1, media=make_doc(1, 10, attrs=[types.DocumentAttributeAudio(duration=3, voice=True)]))
    rnd = make_msg(2, media=make_doc(2, 10, attrs=[types.DocumentAttributeVideo(duration=3, w=1, h=1,
                                                                                    round_message=True)]))
    doc = make_msg(3, media=make_doc(3, 10, name="a.pdf"))
    assert classify_media(voice)[0] == "voice"
    assert classify_media(rnd)[0] == "round"
    kind, info = classify_media(doc)
    assert kind == "document" and info and info["file_name"] == "a.pdf"
    assert classify_media(make_msg(4))[0] is None


def test_message_row_entities_and_links() -> None:
    m = make_msg(5, text="Дивись https://example.com **x**",
                 entities=[types.MessageEntityUrl(offset=7, length=19), types.MessageEntityBold(offset=27, length=5)])
    r = message_row(1, m)
    assert r["has_link"] == 1
    assert r["raw"]["entities"][0]["t"] == "Url"
    assert r["sender_id"] == 2000


def test_find_mini_apps() -> None:
    markup = types.ReplyInlineMarkup(rows=[types.KeyboardInlineButtonRow(buttons=[
        types.KeyboardInlineButton(text="Open", type=types.InlineButtonTypeWebView(url="https://app.example/"))])])
    m = make_msg(6, text="try https://t.me/somegamebot/play?startapp=x", reply_markup=markup)
    apps = find_mini_apps(m)
    kinds = {a["kind"] for a in apps}
    assert "button" in kinds and "app" in kinds
    assert any(a.get("bot_username") == "somegamebot" and a["short_name"] == "play" for a in apps)


class Ctx:
    """Tiny JobContext stand-in for direct calls."""

    def __init__(self) -> None:
        self.progress_data: dict = {}
        self._pause = self._cancel = False

    async def check(self) -> None: ...

    async def progress(self, **kw) -> None:
        kw.pop("force", None)
        self.progress_data.update(kw)


async def test_history_sync_resumes_after_flood_wait(svc: Services) -> None:
    await add_chat(svc)
    msgs = [make_msg(i, text=f"m{i}", days=i // 50) for i in range(1, 251)]
    client = FakeClient(msgs)
    client.flood_once_at = 100  # after the first batch of 100
    use_client(svc, client)
    ctx = Ctx()
    try:
        await sync_chat_history(svc, ctx, 2000)  # type: ignore[arg-type]
        raise AssertionError("expected FloodWait")
    except FloodWait as fw:
        assert fw.seconds == 3
    assert await svc.db.scalar("SELECT count(*) FROM messages WHERE chat_id=2000") == 100
    st = await svc.db.fetchone("SELECT * FROM sync_state WHERE chat_id=2000")
    assert st["last_message_id"] == 100 and st["done"] == 0
    fetched = await sync_chat_history(svc, ctx, 2000)  # type: ignore[arg-type]
    assert fetched == 150
    assert await svc.db.scalar("SELECT count(*) FROM messages WHERE chat_id=2000") == 250
    chat = await svc.db.fetchone("SELECT stored_messages FROM chats WHERE id=2000")
    assert chat["stored_messages"] == 250
    # incremental: new message only
    client.messages.append(make_msg(251, text="new"))
    assert await sync_chat_history(svc, ctx, 2000) == 1  # type: ignore[arg-type]


async def test_media_download_resume_dedup_and_refresh(svc: Services) -> None:
    await add_chat(svc)
    big = bytes(range(256)) * (REQUEST_SIZE * 3 // 256 + 100)  # > 3 chunks
    small = b"x" * 1000
    msgs = [make_msg(1, media=make_doc(11, len(big), name="big.bin")),
            make_msg(2, media=make_doc(12, len(small), name="small.txt")),
            make_msg(3, media=make_doc(11, len(big), name="big-copy.bin")),  # same Telegram file -> dedup
            make_msg(4, text="no media")]
    client = FakeClient(msgs, {11: big, 12: small})
    use_client(svc, client)
    await sync_chat_history(svc, Ctx(), 2000)  # type: ignore[arg-type]
    est = await estimate(svc, [2000], ["document"], {})
    assert est["count"] == 3 and est["bytes"] == 2 * len(big) + len(small)
    assert await enqueue(svc, [2000], ["document"], {"max_size_mb": 10}) == 3

    client.fail_download_after = 2  # network drops mid-file
    client.ref_expired_once = {12}  # small file needs a file_reference refresh
    jid = await svc.engine.submit("download_media", {"chat_ids": [2000]})
    job = await svc.engine.wait(jid, timeout=60)
    assert job["status"] == "done", job
    rows = await svc.db.fetchall("SELECT * FROM media ORDER BY message_id")
    assert [r["status"] for r in rows] == ["done", "done", "done"]
    big_path = Path(rows[0]["path"])
    assert big_path.read_bytes() == big
    assert rows[0]["sha256"] == hashlib.sha256(big).hexdigest()
    assert Path(rows[2]["path"]).read_bytes() == big
    # resumed from an aligned offset, not from zero
    offsets = [o for d, o in client.download_calls if d == 11]
    assert offsets[0] == 0 and offsets[1] == 2 * REQUEST_SIZE
    # dedup: the second copy did not hit the network
    assert len([1 for d, _ in client.download_calls if d == 11]) == 2
    assert Path(rows[1]["path"]).read_bytes() == small
    assert "media" in rows[0]["path"] and "document" in rows[0]["path"]


async def test_protected_chat_media_skipped_until_enabled(svc: Services) -> None:
    from tgarchiver.core.config import Settings

    assert Settings().protected_content is True  # on by default
    svc.settings.protected_content = False
    await add_chat(svc, noforwards=1)
    client = FakeClient([make_msg(1, media=make_doc(21, 100))], {21: b"y" * 100})
    use_client(svc, client)
    await sync_chat_history(svc, Ctx(), 2000)  # type: ignore[arg-type]
    await enqueue(svc, [2000], ["document"], {})
    job = await svc.engine.wait(await svc.engine.submit("download_media", {}), timeout=30)
    assert job["status"] == "done"
    assert await svc.db.scalar("SELECT status FROM media") == "skipped"
    svc.settings.protected_content = True
    await enqueue(svc, [2000], ["document"], {})
    await svc.engine.wait(await svc.engine.submit("download_media", {}), timeout=30)
    assert await svc.db.scalar("SELECT status FROM media") == "done"


async def test_export_job_end_to_end(svc: Services) -> None:
    await add_chat(svc)
    msgs = [make_msg(i, text=f"message {i}", days=i * 10) for i in range(1, 8)]
    msgs.append(make_msg(8, media=make_doc(31, 50, name="doc.pdf"), text="see attached", days=80))
    use_client(svc, FakeClient(msgs, {31: b"z" * 50}))
    jid = await svc.engine.submit("export", {"chat_ids": [2000], "formats": ["md", "html"],
                                             "split": {"mode": "month"}, "media_types": ["document"]})
    job = await svc.engine.wait(jid, timeout=60)
    assert job["status"] == "done", job
    out = svc.account_dir / "chats" / "Alice_2000" / "export"
    idx = (out / "md" / "index.md").read_text(encoding="utf-8")
    assert "2026-" in idx
    assert len(list((out / "md").rglob("*.md"))) >= 3
    html_parts = list((out / "html").rglob("2026-*.html"))
    assert html_parts and "<!doctype html>" in html_parts[0].read_text(encoding="utf-8")
    assert await svc.db.scalar("SELECT status FROM media") == "done"
    md_all = "".join(p.read_text(encoding="utf-8") for p in (out / "md").rglob("2026-*.md"))
    assert "../../../media/document/" in md_all


async def test_out_of_order_writes_do_not_move_checkpoint(svc: Services) -> None:
    """Unread monitor stores the newest messages; the history sync must still fetch the gap before them."""
    from tgarchiver.sync.history import store_batch

    await add_chat(svc)
    msgs = [make_msg(i) for i in range(1, 11)]
    await store_batch(svc, 2000, msgs[8:], 1000, checkpoint=False)
    assert await svc.db.fetchone("SELECT * FROM sync_state WHERE chat_id=2000") is None
    use_client(svc, FakeClient(msgs))
    assert await sync_chat_history(svc, Ctx(), 2000) == 10  # type: ignore[arg-type]


async def test_resync_with_changed_file_requeues_downloaded_media(svc: Services) -> None:
    from tgarchiver.sync.history import store_batch

    await add_chat(svc)
    await store_batch(svc, 2000, [make_msg(1, media=make_doc(11, 10, name="a.bin"))], None)
    await svc.db.execute("UPDATE media SET status='done', sha256='x', bytes_done=10")
    await store_batch(svc, 2000, [make_msg(1, media=make_doc(11, 10, name="a.bin"))], None)
    row = await svc.db.fetchone("SELECT * FROM media")
    assert row["status"] == "done" and row["sha256"] == "x"  # same file: untouched
    await store_batch(svc, 2000, [make_msg(1, media=make_doc(99, 20, name="b.bin"))], None)
    row = await svc.db.fetchone("SELECT * FROM media")
    assert row["status"] == "pending" and row["sha256"] is None and row["bytes_done"] == 0
    assert row["file_name"] == "b.bin" and row["size"] == 20


async def test_chat_preview_fetches_latest_without_moving_checkpoint(svc: Services) -> None:
    await add_chat(svc)
    client = FakeClient([make_msg(i, text=f"m{i}") for i in range(1, 251)], {})
    use_client(svc, client)
    job = await svc.engine.wait(await svc.engine.submit("chat_preview", {"chat_id": 2000}), timeout=30)
    assert job["status"] == "done" and job["progress"]["result"]["fetched"] == 100
    ids = [r["id"] for r in await svc.db.fetchall("SELECT id FROM messages WHERE chat_id=2000 ORDER BY id")]
    assert ids[0] == 151 and ids[-1] == 250
    assert not await svc.db.fetchone("SELECT 1 FROM sync_state WHERE chat_id=2000 AND last_message_id > 0")
    assert await svc.db.scalar("SELECT total FROM sync_state WHERE chat_id=2000") == 250
    # opening again only pulls what is new
    client.messages.append(make_msg(251, text="new"))
    job = await svc.engine.wait(await svc.engine.submit("chat_preview", {"chat_id": 2000}), timeout=30)
    assert job["progress"]["result"]["fetched"] == 1
    # the full sync still fetches the whole history from the start
    await sync_chat_history(svc, Ctx(), 2000)  # type: ignore[arg-type]
    assert await svc.db.scalar("SELECT count(*) FROM messages WHERE chat_id=2000") == 251


async def test_unavailable_file_fails_fast_and_does_not_stall_queue(svc: Services) -> None:
    import time as _time

    from telethon import errors

    await add_chat(svc)
    msgs = [make_msg(1, media=make_doc(31, 10, name="gone.bin"))] + [
        make_msg(i, media=make_doc(100 + i, 50, name=f"f{i}.bin")) for i in range(2, 8)]
    client = FakeClient(msgs, {100 + i: b"z" * 50 for i in range(2, 8)})
    orig = client.iter_download

    def iter_download(media, **kw):  # type: ignore[no-untyped-def]
        if media.id == 31:
            raise errors.FileReferenceExpiredError(request=None)
        return orig(media, **kw)

    client.iter_download = iter_download  # type: ignore[method-assign]
    use_client(svc, client)
    await sync_chat_history(svc, Ctx(), 2000)  # type: ignore[arg-type]
    await enqueue(svc, [2000], ["document"], {})
    t0 = _time.monotonic()
    job = await svc.engine.wait(await svc.engine.submit("download_media", {"chat_ids": [2000]}), timeout=30)
    assert job["status"] == "done"
    assert _time.monotonic() - t0 < 5  # no retry backoff spent on a file that can't be downloaded
    rows = {r["message_id"]: r for r in await svc.db.fetchall("SELECT * FROM media")}
    assert rows[1]["status"] == "failed" and rows[1]["attempts"] == 1 and "file_unavailable" in rows[1]["error"]
    assert all(rows[i]["status"] == "done" for i in range(2, 8))
