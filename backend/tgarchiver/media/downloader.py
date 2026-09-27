"""Media queue: chunked resumable downloads, pause, retries, file_reference refresh, dedup (docs/05)."""

from __future__ import annotations

import asyncio
import errno
import hashlib
import logging
import os
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from telethon import errors

from tgarchiver.core.db import now_iso
from tgarchiver.core.paths import long_path, safe_name, slug
from tgarchiver.jobs.engine import Deferred, FloodWait, JobContext, JobPaused
from tgarchiver.sync.dialogs import input_peer

if TYPE_CHECKING:
    from tgarchiver.services import Services

log = logging.getLogger(__name__)

REQUEST_SIZE = 512 * 1024
PERSIST_EVERY_S = 1.5
MEDIA_TYPES = ("photo", "video", "round", "voice", "audio", "document", "sticker", "gif")


def chat_dir(svc: Services, chat: dict[str, Any]) -> Path:
    return svc.account_dir / "chats" / f"{slug(chat['title'] or 'chat')}_{abs(int(chat['id']))}"


def media_path(svc: Services, chat: dict[str, Any], row: dict[str, Any]) -> Path:
    d = datetime.fromisoformat(row["date"]) if row.get("date") else datetime.now()
    name = safe_name(row.get("file_name") or f"{row['message_id']}", max_len=80)
    rel = svc.settings.path_template.format(
        type=row["type"], yyyy=f"{d.year:04d}", mm=f"{d.month:02d}", msgid=row["message_id"], name=name
    )
    parts = [safe_name(p, max_len=90) for p in Path(rel).parts]
    return chat_dir(svc, chat).joinpath("media", *parts)


def _filters_sql(chat_ids: list[int] | None, types_: list[str] | None, f: dict[str, Any]) -> tuple[str, list[Any]]:
    where = ["1=1"]
    args: list[Any] = []
    if chat_ids:
        where.append(f"d.chat_id IN ({','.join('?' * len(chat_ids))})")
        args.extend(chat_ids)
    if types_ is not None:
        if not types_:
            where.append("0")
        else:
            where.append(f"d.type IN ({','.join('?' * len(types_))})")
            args.extend(types_)
    if f.get("max_size_mb"):
        where.append("d.size <= ?")
        args.append(int(float(f["max_size_mb"]) * 1024 * 1024))
    if f.get("date_from"):
        where.append("d.date >= ?")
        args.append(f["date_from"])
    if f.get("date_to"):
        where.append("d.date <= ?")
        args.append(f["date_to"] + "T23:59:59" if len(f["date_to"]) == 10 else f["date_to"])
    if f.get("from") == "me":
        where.append("m.out = 1")
    elif f.get("from") == "others":
        where.append("m.out = 0")
    exts = [e.lower().lstrip(".") for e in f.get("extensions") or [] if e]
    if exts:
        where.append("(d.type != 'document' OR (" + " OR ".join("lower(d.file_name) LIKE ?" for _ in exts) + "))")
        args.extend(f"%.{e}" for e in exts)
    return " AND ".join(where), args


async def estimate(svc: Services, chat_ids: list[int] | None, types_: list[str] | None,
                   filters: dict[str, Any]) -> dict[str, Any]:
    where, args = _filters_sql(chat_ids, types_, filters)
    rows = await svc.db.fetchall(
        f"SELECT d.type, count(*) AS n, coalesce(sum(d.size),0) AS bytes, "
        f"sum(CASE WHEN d.status='done' THEN 1 ELSE 0 END) AS done FROM media d "
        f"JOIN messages m ON m.chat_id=d.chat_id AND m.id=d.message_id WHERE {where} GROUP BY d.type", args)
    return {
        "by_type": {r["type"]: {"count": r["n"], "bytes": r["bytes"], "done": r["done"]} for r in rows},
        "count": sum(r["n"] for r in rows),
        "bytes": sum(r["bytes"] for r in rows),
    }


async def enqueue(svc: Services, chat_ids: list[int] | None, types_: list[str], filters: dict[str, Any],
                  priority: int = 0) -> int:
    where, args = _filters_sql(chat_ids, types_, filters)
    return await svc.db.execute(
        f"UPDATE media SET status='pending', priority=max(priority, ?), attempts=0, error=NULL, updated_at=? "
        f"WHERE id IN (SELECT d.id FROM media d JOIN messages m ON m.chat_id=d.chat_id AND m.id=d.message_id "
        f"WHERE {where} AND d.status IN ('available','failed','skipped','paused'))",
        [priority, now_iso(), *args],
    )


def _in_window(window: str, now: datetime) -> tuple[bool, int]:
    """'01:00-07:00' -> (inside?, seconds until the window opens)."""
    try:
        a, b = window.split("-")
        ah, am = map(int, a.split(":"))
        bh, bm = map(int, b.split(":"))
    except ValueError:
        return True, 0
    start, end, cur = ah * 60 + am, bh * 60 + bm, now.hour * 60 + now.minute
    inside = start <= cur < end if start <= end else (cur >= start or cur < end)
    if inside:
        return True, 0
    wait = (start - cur) % (24 * 60)
    return False, max(60, wait * 60 - now.second)


class FileStopped(Exception):  # noqa: N818
    """The user paused/cancelled this single file while it was downloading."""


class MediaQueue:
    """Runs inside a `download_media` job. Per-file state lives in the `media` table."""

    def __init__(self, svc: Services, ctx: JobContext) -> None:
        self.svc = svc
        self.ctx = ctx
        self.chat_ids: list[int] | None = ctx.params.get("chat_ids") or None
        self.bytes_session = 0
        self.session_total = 0
        self.started = time.monotonic()
        self.chats: dict[int, dict[str, Any]] = {}

    async def _check_schedule(self) -> None:
        s = self.svc.settings
        if s.download_window:
            inside, wait = _in_window(s.download_window, datetime.now())
            if not inside:
                raise Deferred(wait, "download_window")
        if s.daily_gb_limit > 0:
            today = datetime.now().date().isoformat()
            used = await self.svc.db.kv_get(f"bytes_{today}", 0)
            if used >= s.daily_gb_limit * 1024**3:
                secs = int((datetime.combine(datetime.now().date(), datetime.min.time()).timestamp()
                            + 86400) - time.time())
                raise Deferred(max(60, secs), "daily_limit")

    async def _scope_counts(self) -> dict[str, Any]:
        where = "status IN ('pending','downloading','done','failed')"
        args: list[Any] = []
        if self.chat_ids:
            where += f" AND chat_id IN ({','.join('?' * len(self.chat_ids))})"
            args = list(self.chat_ids)
        row = await self.svc.db.fetchone(
            f"SELECT count(*) AS total, sum(CASE WHEN status='done' THEN 1 ELSE 0 END) AS done, "
            f"coalesce(sum(size),0) AS bytes_total, coalesce(sum(CASE WHEN status='done' THEN size ELSE bytes_done END),0)"
            f" AS bytes_done, sum(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed FROM media WHERE {where}",
            args)
        return row or {}

    async def _next_batch(self) -> list[dict[str, Any]]:
        where = "status='pending' AND attempts < ?"
        args: list[Any] = [self.svc.settings.max_retries]
        if self.chat_ids:
            where += f" AND chat_id IN ({','.join('?' * len(self.chat_ids))})"
            args.extend(self.chat_ids)
        # Manual "download now" first, then small files before big ones.
        return await self.svc.db.fetchall(
            f"SELECT * FROM media WHERE {where} ORDER BY priority DESC, chat_id, size ASC LIMIT 100", args)

    async def run(self) -> dict[str, Any]:
        while True:
            await self.ctx.check()
            await self._check_schedule()
            batch = await self._next_batch()
            if not batch:
                break
            by_chat: dict[int, list[dict[str, Any]]] = {}
            for r in batch:
                by_chat.setdefault(r["chat_id"], []).append(r)
            for chat_id, rows in by_chat.items():
                await self._download_chat_batch(chat_id, rows)
        from tgarchiver.sync.history import refresh_chat_counters

        for cid in self.chats:
            await refresh_chat_counters(self.svc, cid)
        counts = await self._scope_counts()
        await self.ctx.progress(**counts, force=True)
        self.svc.bus.emit("media.changed")
        return counts

    async def _chat(self, chat_id: int) -> dict[str, Any]:
        if chat_id not in self.chats:
            row = await self.svc.db.fetchone("SELECT * FROM chats WHERE id=?", (chat_id,))
            if not row:
                raise ValueError(f"chat {chat_id} missing")
            self.chats[chat_id] = row
        return self.chats[chat_id]

    async def _download_chat_batch(self, chat_id: int, rows: list[dict[str, Any]]) -> None:
        chat = await self._chat(chat_id)
        if chat["noforwards"] and not self.svc.settings.protected_content:
            await self.svc.db.execute(
                f"UPDATE media SET status='skipped', error='protected_disabled' WHERE id IN "
                f"({','.join('?' * len(rows))})", [r["id"] for r in rows])
            return
        # Dedup: identical Telegram file already downloaded elsewhere -> hardlink/copy, no network.
        todo = []
        file_ids: set[int] = set()
        for r in rows:
            if await self._try_dedup(chat, r):
                continue
            if r["tg_file_id"] in file_ids:
                continue  # same file already in this round; the copy is hardlinked on the next round
            file_ids.add(r["tg_file_id"])
            todo.append(r)
        if not todo:
            return
        client = await self.svc.tg.authorized_client()
        peer = input_peer(chat)
        ids = [r["message_id"] for r in todo]
        msgs = await self.svc.tg.call(lambda: client.get_messages(peer, ids=ids))
        by_id = {m.id: m for m in msgs if m is not None}
        sem = self.svc.tg.media_sem
        errors_seen: list[BaseException] = []

        async def one(r: dict[str, Any]) -> None:
            async with sem:
                if errors_seen:
                    return
                # Atomic claim: another queue (export job vs. download job) may be working on the same scope.
                claimed = await self.svc.db.execute(
                    "UPDATE media SET status='downloading', updated_at=? WHERE id=? AND status='pending'",
                    (now_iso(), r["id"]))
                if not claimed:
                    return
                try:
                    await self._download_one(client, peer, chat, r, by_id.get(r["message_id"]))
                except FileStopped:
                    pass  # the row already carries the user's paused/skipped status
                except (FloodWait, JobPaused, Deferred) as e:
                    errors_seen.append(e)
                    await self._release(r)
                except asyncio.CancelledError:
                    await self._release(r)
                    raise
                except Exception as e:  # noqa: BLE001
                    await self._fail(r, e)

        tasks = [asyncio.create_task(one(r)) for r in todo]
        try:
            await asyncio.gather(*tasks)
        finally:
            for t in tasks:
                t.cancel()
        for e in errors_seen:
            raise e
        await self.ctx.check()

    async def _try_dedup(self, chat: dict[str, Any], r: dict[str, Any]) -> bool:
        if not r["tg_file_id"]:
            return False
        src = await self.svc.db.fetchone(
            "SELECT path, sha256 FROM media WHERE tg_file_id=? AND status='done' AND id!=? AND path IS NOT NULL "
            "LIMIT 1", (r["tg_file_id"], r["id"]))
        if not src or not Path(src["path"]).exists():
            return False
        dst = media_path(self.svc, chat, r)
        dst.parent.mkdir(parents=True, exist_ok=True)
        if not dst.exists():
            try:
                os.link(src["path"], long_path(dst))
            except OSError:
                shutil.copy2(src["path"], long_path(dst))
        await self.svc.db.execute(
            "UPDATE media SET status='done', path=?, sha256=?, bytes_done=size, updated_at=? WHERE id=?",
            (str(dst), src["sha256"], now_iso(), r["id"]))
        return True

    async def _release(self, r: dict[str, Any]) -> None:
        await self.svc.db.execute("UPDATE media SET status='pending' WHERE id=? AND status='downloading'", (r["id"],))

    async def _fail(self, r: dict[str, Any], e: BaseException) -> None:
        attempts = r["attempts"] + 1
        final = attempts >= self.svc.settings.max_retries
        log.warning("media %s failed (%s/%s): %s", r["id"], attempts, self.svc.settings.max_retries, type(e).__name__)
        await self.svc.db.execute(
            "UPDATE media SET status=?, attempts=?, error=?, updated_at=? WHERE id=? AND status='downloading'",
            ("failed" if final else "pending", attempts, f"{type(e).__name__}: {e}"[:300], now_iso(), r["id"]))
        if not final:
            await asyncio.sleep(min(2 ** attempts, 30))

    async def _download_one(self, client: Any, peer: Any, chat: dict[str, Any], r: dict[str, Any],
                            msg: Any) -> None:
        if msg is None or msg.media is None:
            await self.svc.db.execute("UPDATE media SET status='skipped', error='message_gone' WHERE id=?", (r["id"],))
            return
        final = media_path(self.svc, chat, r)
        final.parent.mkdir(parents=True, exist_ok=True)
        part = final.with_name(final.name + ".part")
        marker = final.with_name(final.name + ".part.id")
        size = int(r["size"] or 0)
        file_id = str(r["tg_file_id"] or "")
        if part.exists() and (not marker.exists() or marker.read_text(encoding="utf-8").strip() != file_id):
            part.unlink()  # leftover of a different file (edited message) - never append to it
        marker.write_text(file_id, encoding="utf-8")
        # Resume from what is on disk, aligned to request size (Telegram requires aligned offsets).
        on_disk = part.stat().st_size if part.exists() else 0
        offset = (on_disk // REQUEST_SIZE) * REQUEST_SIZE
        await self.svc.db.execute("UPDATE media SET path=?, bytes_done=?, updated_at=? WHERE id=?",
                                  (str(final), offset, now_iso(), r["id"]))
        refreshed = False
        while True:
            try:
                await self._stream(client, msg, part, offset, size, r)
                break
            except (errors.FileReferenceExpiredError, errors.FileReferenceInvalidError):
                if refreshed:
                    raise
                refreshed = True
                fresh = await self.svc.tg.call(lambda: client.get_messages(peer, ids=r["message_id"]))
                if fresh is None or fresh.media is None:
                    raise
                msg = fresh
                offset = (part.stat().st_size // REQUEST_SIZE) * REQUEST_SIZE if part.exists() else 0
            except OSError as e:
                if e.errno == errno.ENOSPC:
                    self.svc.bus.emit("notification", {"code": "disk_full"})
                    raise Deferred(600, "disk_full") from e
                raise
        got = part.stat().st_size
        # Photo byte counts in the catalog are estimates from PhotoSize; only documents have an exact size.
        if size and got < size and getattr(msg, "document", None) is not None:
            raise OSError(f"incomplete download {got}/{size}")
        sha = await asyncio.to_thread(_sha256, part)
        if final.exists():
            final.unlink()
        part.replace(final)
        marker.unlink(missing_ok=True)
        await self.svc.db.execute(
            "UPDATE media SET status='done', bytes_done=?, size=CASE WHEN size>0 THEN size ELSE ? END, sha256=?, "
            "error=NULL, updated_at=? WHERE id=?", (got, got, sha, now_iso(), r["id"]))
        self.svc.bus.emit("media.done", {"id": r["id"], "chat_id": r["chat_id"]})

    async def _stream(self, client: Any, msg: Any, part: Path, offset: int, size: int, r: dict[str, Any]) -> None:
        media = msg.photo or msg.document or msg.media
        with open(long_path(part), "r+b" if part.exists() else "wb") as fh:
            fh.truncate(offset)
            fh.seek(offset)
            done = offset
            last = time.monotonic()
            try:
                exact = size if getattr(msg, "document", None) is not None else None
                async for chunk in client.iter_download(media, offset=offset, request_size=REQUEST_SIZE,
                                                        file_size=exact or None):
                    fh.write(chunk)
                    done += len(chunk)
                    self.bytes_session += len(chunk)
                    self.session_total += len(chunk)
                    now = time.monotonic()
                    if now - last >= PERSIST_EVERY_S:
                        last = now
                        fh.flush()
                        await self._persist_progress(r, done)
                    if self.ctx._pause or self.ctx._cancel:
                        fh.flush()
                        await self._persist_progress(r, done)
                        await self.ctx.check()
            except (errors.FloodWaitError, errors.FloodPremiumWaitError) as e:
                fh.flush()
                await self._persist_progress(r, done)
                raise FloodWait(e.seconds) from e
            fh.flush()
            await self._persist_progress(r, done, force=True)

    async def _persist_progress(self, r: dict[str, Any], done: int, force: bool = False) -> None:
        still = await self.svc.db.execute(
            "UPDATE media SET bytes_done=?, updated_at=? WHERE id=? AND status='downloading'",
            (done, now_iso(), r["id"]))
        today = datetime.now().date().isoformat()
        if self.bytes_session:
            await self.svc.db.kv_add(f"bytes_{today}", self.bytes_session)
            self.bytes_session = 0
        if not still:
            raise FileStopped()
        elapsed = max(time.monotonic() - self.started, 0.001)
        counts = await self._scope_counts()
        speed = self.session_total / elapsed
        await self.ctx.progress(**counts, speed=int(speed), current={"id": r["id"], "name": r["file_name"], "done": done,
                                                   "size": r["size"]}, force=force)
        self.svc.bus.emit("media.progress", {"id": r["id"], "bytes_done": done, "size": r["size"],
                                             "speed": speed}, throttle_key=f"media:{r['id']}")


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(long_path(p), "rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


async def download_media_job(svc: Services, ctx: JobContext) -> dict[str, Any]:
    return await MediaQueue(svc, ctx).run()
