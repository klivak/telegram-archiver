"""Incremental message history download with per-batch checkpoints (docs/04)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from tgarchiver.core.db import dumps, now_iso
from tgarchiver.jobs.engine import JobContext
from tgarchiver.sync.dialogs import input_peer
from tgarchiver.sync.normalize import find_mini_apps, message_row, user_row

if TYPE_CHECKING:
    from tgarchiver.services import Services

log = logging.getLogger(__name__)

BATCH = 100
MSG_COLS = ("chat_id", "id", "topic_id", "sender_id", "out", "date", "edit_date", "text", "reply_to", "fwd_from",
            "media_type", "has_media", "has_link", "reactions", "buttons", "service_action", "noforwards",
            "grouped_id", "raw")
_JSON_COLS = {"fwd_from", "reactions", "buttons", "raw"}


async def get_chat(svc: Services, chat_id: int) -> dict[str, Any]:
    row = await svc.db.fetchone("SELECT * FROM chats WHERE id=?", (chat_id,))
    if not row:
        raise ValueError(f"unknown chat {chat_id}")
    return row


async def store_batch(svc: Services, chat_id: int, messages: list[Any], me_id: int | None, *,
                      checkpoint: bool = True) -> int:
    """Upsert one batch of Telethon messages + senders + media catalog + Mini App candidates, and checkpoint.

    Pass checkpoint=False for out-of-order writes (unread monitor, live updates): advancing sync_state there
    would make the next history sync skip the gap before them.
    """
    msg_rows, media_rows, user_rows, apps = [], [], {}, []
    max_id = 0
    for m in messages:
        r = message_row(chat_id, m)
        if r["out"] and r["sender_id"] is None:
            r["sender_id"] = me_id
        info = r.pop("_media")
        mtype = r.pop("_media_type")
        msg_rows.append(tuple(dumps(r[c]) if c in _JSON_COLS else r[c] for c in MSG_COLS))
        if info:
            media_rows.append((chat_id, m.id, info["tg_file_id"], mtype, info.get("mime"), info.get("size", 0),
                               info.get("file_name"), info.get("duration"), r["date"], now_iso()))
        sender = getattr(m, "sender", None)
        if sender is not None:
            u = user_row(sender)
            if u:
                user_rows[u["id"]] = u
        for app in find_mini_apps(m):
            apps.append((app, m))
        max_id = max(max_id, m.id)

    ph = ",".join("?" * len(MSG_COLS))
    upd = ",".join(f"{c}=excluded.{c}" for c in MSG_COLS if c not in ("chat_id", "id"))
    async with svc.db.tx() as c:
        if user_rows:
            await c.executemany(
                "INSERT INTO users(id, first_name, last_name, username, phone, is_bot) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET first_name=excluded.first_name, last_name=excluded.last_name, "
                "username=excluded.username, phone=coalesce(excluded.phone, users.phone), is_bot=excluded.is_bot",
                [(u["id"], u["first_name"], u["last_name"], u["username"], u["phone"], u["is_bot"])
                 for u in user_rows.values()],
            )
        await c.executemany(
            f"INSERT INTO messages({','.join(MSG_COLS)}) VALUES({ph}) ON CONFLICT(chat_id, id) DO UPDATE SET {upd}",
            msg_rows,
        )
        if media_rows:
            await c.executemany(
                "INSERT INTO media(chat_id, message_id, tg_file_id, type, mime, size, file_name, duration, date, "
                "updated_at, status) VALUES(?,?,?,?,?,?,?,?,?,?,'available') ON CONFLICT(chat_id, message_id) DO "
                "UPDATE SET tg_file_id=excluded.tg_file_id, size=excluded.size, "
                # edited message with a different file: re-queue it if the old one was already fetched
                "status=CASE WHEN media.tg_file_id IS NOT excluded.tg_file_id AND media.status IN ('done','failed') "
                "THEN 'pending' ELSE media.status END, "
                "bytes_done=CASE WHEN media.tg_file_id IS NOT excluded.tg_file_id THEN 0 ELSE media.bytes_done END, "
                "sha256=CASE WHEN media.tg_file_id IS NOT excluded.tg_file_id THEN NULL ELSE media.sha256 END, "
                "attempts=CASE WHEN media.tg_file_id IS NOT excluded.tg_file_id THEN 0 ELSE media.attempts END, "
                "type=excluded.type, mime=excluded.mime, file_name=excluded.file_name, duration=excluded.duration",
                media_rows,
            )
        for app, m in apps:
            sender_is_bot = bool(getattr(getattr(m, "sender", None), "bot", False))
            bot_id = getattr(m, "via_bot_id", None) or (m.sender_id if sender_is_bot else None)
            if bot_id is None and app["kind"] in ("button", "simple"):
                chat_type = await (await c.execute("SELECT type FROM chats WHERE id=?", (chat_id,))).fetchone()
                bot_id = chat_id if chat_type and chat_type[0] == "bot" else None
            await c.execute(
                "INSERT INTO mini_apps(bot_id, bot_username, kind, short_name, title, url, found_in_chat, "
                "found_in_msg, first_seen, last_seen) VALUES(?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(bot_id, kind, short_name, url) DO UPDATE SET last_seen=excluded.last_seen",
                (bot_id, app.get("bot_username"), app["kind"], app.get("short_name", ""), app.get("title"),
                 _strip_init_data(app.get("url") or ""), chat_id, m.id, now_iso(), now_iso()),
            )
        if max_id and checkpoint:
            await c.execute(
                "INSERT INTO sync_state(chat_id, topic_id, last_message_id, done, updated_at) VALUES(?,0,?,0,?) "
                "ON CONFLICT(chat_id, topic_id) DO UPDATE SET last_message_id=max(sync_state.last_message_id, "
                "excluded.last_message_id), updated_at=excluded.updated_at",
                (chat_id, max_id, now_iso()),
            )
    return max_id


def _strip_init_data(url: str) -> str:
    return url.split("#tgWebAppData", 1)[0].split("tgWebAppData=", 1)[0]


async def sync_chat_history(svc: Services, ctx: JobContext, chat_id: int, *, client: Any = None,
                            progress_prefix: dict[str, Any] | None = None) -> int:
    """Download all new messages of a chat, oldest first, resuming from sync_state."""
    chat = await get_chat(svc, chat_id)
    client = client or await svc.tg.authorized_client()
    me_id = (svc.tg.me or {}).get("id")
    peer = input_peer(chat)
    state = await svc.db.fetchone("SELECT * FROM sync_state WHERE chat_id=? AND topic_id=0", (chat_id,))
    last_id = int(state["last_message_id"]) if state else 0
    have = int(await svc.db.scalar("SELECT count(*) FROM messages WHERE chat_id=?", (chat_id,)) or 0)
    head = await svc.tg.call(lambda: client.get_messages(peer, limit=1))
    total = int(getattr(head, "total", 0) or 0)
    await svc.db.execute(
        "INSERT INTO sync_state(chat_id, topic_id, last_message_id, done, total, updated_at) VALUES(?,0,?,0,?,?) "
        "ON CONFLICT(chat_id, topic_id) DO UPDATE SET total=excluded.total", (chat_id, last_id, total, now_iso()))
    fields = {**(progress_prefix or {}), "stage": "messages", "chat_id": chat_id, "chat_title": chat["title"],
              "msg_done": have, "msg_total": max(total, have)}
    await ctx.progress(**fields, force=True)
    fetched = 0
    while True:
        await ctx.check()
        batch = await svc.tg.call(
            lambda lid=last_id: client.get_messages(peer, limit=BATCH, min_id=lid, reverse=True))  # type: ignore[misc]
        batch = [m for m in batch if m is not None and m.id > last_id]
        if not batch:
            break
        last_id = await store_batch(svc, chat_id, batch, me_id)
        fetched += len(batch)
        await ctx.progress(msg_done=have + fetched, msg_total=max(total, have + fetched))
        if len(batch) < BATCH:
            break
    await svc.db.execute("UPDATE sync_state SET done=1, updated_at=? WHERE chat_id=? AND topic_id=0",
                         (now_iso(), chat_id))
    await refresh_chat_counters(svc, chat_id)
    return fetched


PREVIEW_LIMIT = 100


async def chat_preview_job(svc: Services, ctx: JobContext) -> dict[str, Any]:
    """Fetch the latest messages of one chat when it is opened, without touching the full-sync checkpoint."""
    chat_id = int(ctx.params["chat_id"])
    chat = await get_chat(svc, chat_id)
    client = await svc.tg.authorized_client()
    newest = int(await svc.db.scalar("SELECT max(id) FROM messages WHERE chat_id=?", (chat_id,)) or 0)
    raw = await svc.tg.call(lambda: client.get_messages(input_peer(chat), limit=PREVIEW_LIMIT, min_id=newest))
    total = int(getattr(raw, "total", 0) or 0)
    if total:  # remember the chat size so the UI knows how much history is still missing; checkpoint untouched
        await svc.db.execute(
            "INSERT INTO sync_state(chat_id, topic_id, last_message_id, done, total, updated_at) VALUES(?,0,0,0,?,?) "
            "ON CONFLICT(chat_id, topic_id) DO UPDATE SET total=max(sync_state.total, excluded.total)",
            (chat_id, total, now_iso()))
    batch = [m for m in raw if m is not None and m.id > newest]
    if batch:
        await store_batch(svc, chat_id, batch, (svc.tg.me or {}).get("id"), checkpoint=False)
        await refresh_chat_counters(svc, chat_id)
    svc.bus.emit("chat.preview", {"chat_id": chat_id, "fetched": len(batch)})
    return {"fetched": len(batch)}


async def refresh_chat_counters(svc: Services, chat_id: int) -> None:
    await svc.db.execute(
        "UPDATE chats SET stored_messages=(SELECT count(*) FROM messages WHERE chat_id=?), "
        "media_count=(SELECT count(*) FROM media WHERE chat_id=?), "
        "media_done=(SELECT count(*) FROM media WHERE chat_id=? AND status='done') WHERE id=?",
        (chat_id, chat_id, chat_id, chat_id))


async def sync_history_job(svc: Services, ctx: JobContext) -> dict[str, Any]:
    chat_ids: list[int] = list(ctx.params.get("chat_ids", []))
    start = int(ctx.checkpoint.get("index", 0))
    total_new = int(ctx.checkpoint.get("fetched", 0))
    for i in range(start, len(chat_ids)):
        await ctx.progress(chats_done=i, chats_total=len(chat_ids), force=True)
        total_new += await sync_chat_history(svc, ctx, chat_ids[i])
        await ctx.save_checkpoint(index=i + 1, fetched=total_new)
    await ctx.progress(chats_done=len(chat_ids), chats_total=len(chat_ids), force=True)
    svc.bus.emit("chats.changed")
    return {"fetched": total_new}
