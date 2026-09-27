"""Unread monitor: collects unread messages WITHOUT marking them read (docs/10) + real-time new messages."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from telethon import events, utils
from telethon.tl.functions.account import UpdateStatusRequest

from tgarchiver.jobs.engine import JobContext
from tgarchiver.sync.dialogs import input_peer, upsert_chats
from tgarchiver.sync.history import store_batch
from tgarchiver.sync.normalize import chat_row

if TYPE_CHECKING:
    from tgarchiver.services import Services

log = logging.getLogger(__name__)
MAX_PER_CHAT = 300


async def scope_chats(svc: Services) -> list[dict[str, Any]]:
    m = svc.settings.monitor
    rows = await svc.db.fetchall("SELECT * FROM chats WHERE unread_count > 0 ORDER BY last_message_at DESC")
    allowed: set[int] | None = None
    if m.chat_ids or m.folder_ids:
        allowed = set(m.chat_ids)
        if m.folder_ids:
            fr = await svc.db.fetchall(
                f"SELECT chat_id FROM folder_chats WHERE folder_id IN ({','.join('?' * len(m.folder_ids))})",
                m.folder_ids)
            allowed |= {r["chat_id"] for r in fr}
    out = []
    for r in rows:
        if r["id"] in m.ignore_chat_ids or (allowed is not None and r["id"] not in allowed):
            continue
        if m.mentions_only and not r["unread_mentions"] and r["type"] not in ("user", "saved"):
            continue
        out.append(r)
    return out


async def monitor_job(svc: Services, ctx: JobContext) -> dict[str, Any]:
    client = await svc.tg.authorized_client()
    me_id = (svc.tg.me or await svc.tg.load_me() or {}).get("id")
    if svc.settings.monitor.stay_offline:
        await svc.tg.call(lambda: client(UpdateStatusRequest(offline=True)), limited=False)
    # Refresh unread counters (reading the dialog list does not mark anything as read).
    rows: list[dict[str, Any]] = []

    async def collect() -> None:
        async for d in client.iter_dialogs(limit=500):
            rows.append(chat_row(d, me_id))

    await svc.tg.call(collect)
    await upsert_chats(svc, rows)
    chats = await scope_chats(svc)
    fetched = 0
    for i, chat in enumerate(chats):
        await ctx.check()
        await ctx.progress(done=i, total=len(chats), chat_title=chat["title"])
        peer = input_peer(chat)
        limit = min(int(chat["unread_count"]), MAX_PER_CHAT)
        min_id = int(chat["read_inbox_max_id"] or 0)
        msgs = await svc.tg.call(
            lambda peer=peer, limit=limit, min_id=min_id: client.get_messages(peer, limit=limit, min_id=min_id)  # type: ignore[misc]
        )
        msgs = [m for m in msgs if m is not None]
        if msgs:
            msgs.sort(key=lambda m: m.id)
            await store_batch(svc, chat["id"], msgs, me_id, checkpoint=False)
            fetched += len(msgs)
    await svc.db.kv_set("monitor_last_run", datetime.now().isoformat(timespec="seconds"))
    svc.bus.emit("monitor.updated", {"chats": len(chats), "fetched": fetched})
    return {"chats": len(chats), "fetched": fetched}


async def unread_overview(svc: Services) -> list[dict[str, Any]]:
    chats = await scope_chats(svc)
    out = []
    for c in chats:
        n = await svc.db.scalar("SELECT count(*) FROM messages WHERE chat_id=? AND id>? AND out=0",
                                (c["id"], c["read_inbox_max_id"] or 0))
        last = await svc.db.fetchall(
            "SELECT m.id, m.date, m.text, m.media_type, u.first_name, u.last_name FROM messages m LEFT JOIN users u "
            "ON u.id=m.sender_id WHERE m.chat_id=? AND m.id>? AND m.out=0 ORDER BY m.id DESC LIMIT 3",
            (c["id"], c["read_inbox_max_id"] or 0))
        out.append({"chat_id": c["id"], "title": c["title"], "type": c["type"], "unread": c["unread_count"],
                    "mentions": c["unread_mentions"], "stored": n, "last_message_at": c["last_message_at"],
                    "preview": last})
    return out


class MonitorService:
    """Background loop: periodic unread collection, real-time handler, scheduled AI digest."""

    def __init__(self, svc: Services) -> None:
        self.svc = svc
        self.task: asyncio.Task[Any] | None = None
        self._handler_installed = False

    def start(self) -> None:
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._loop(), name="monitor")

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except BaseException:  # noqa: BLE001
                pass

    async def _install_handler(self) -> None:
        if self._handler_installed or not await self.svc.tg.is_authorized():
            return
        client = await self.svc.tg.authorized_client()

        async def on_new(event: Any) -> None:
            try:
                chat_id = utils.get_peer_id(event.message.peer_id)
                known = await self.svc.db.scalar("SELECT 1 FROM chats WHERE id=?", (chat_id,))
                if known:
                    await store_batch(self.svc, chat_id, [event.message], (self.svc.tg.me or {}).get("id"),
                                      checkpoint=False)
                    if not event.message.out:
                        await self.svc.db.execute("UPDATE chats SET unread_count=unread_count+1, last_message_at=?, "
                                                  "last_message_id=? WHERE id=?",
                                                  (event.message.date.isoformat(timespec="seconds"), event.message.id,
                                                   chat_id))
                self.svc.bus.emit("new_message", {"chat_id": chat_id, "id": event.message.id,
                                                  "out": bool(event.message.out)})
                if self.svc.settings.whisper.auto and chat_id in self.svc.settings.whisper.auto_chat_ids:
                    row = await self.svc.db.fetchone(
                        "SELECT id FROM media WHERE chat_id=? AND message_id=? AND type IN ('voice','round')",
                        (chat_id, event.message.id))
                    if row:
                        await self.svc.engine.submit("transcribe", {"media_ids": [row["id"]]}, title="transcribe")
            except Exception:  # noqa: BLE001
                log.exception("new message handler failed")

        client.add_event_handler(on_new, events.NewMessage())
        self._handler_installed = True

    async def _loop(self) -> None:
        last_monitor = 0.0
        last_digest_day = await self.svc.db.kv_get("ai_last_scheduled", "")
        while True:
            try:
                s = self.svc.settings
                if s.monitor.enabled:
                    await self._install_handler()
                    now = asyncio.get_running_loop().time()
                    if now - last_monitor >= s.monitor.interval_min * 60:
                        last_monitor = now
                        active = await self.svc.db.scalar(
                            "SELECT count(*) FROM jobs WHERE kind='monitor' AND status IN ('queued','running')")
                        if not active:
                            await self.svc.engine.submit("monitor", {}, title="monitor")
                if s.ai.enabled and s.ai.schedule_time:
                    today = datetime.now().date().isoformat()
                    if last_digest_day != today and datetime.now().strftime("%H:%M") >= s.ai.schedule_time:
                        last_digest_day = today
                        await self.svc.db.kv_set("ai_last_scheduled", today)
                        await self.svc.engine.submit("ai", {"task": "digest", "scope": {"unread": True},
                                                            "refresh_unread": True, "scheduled": True},
                                                     title="ai_digest")
            except Exception:  # noqa: BLE001
                log.exception("monitor loop error")
            await asyncio.sleep(20)
