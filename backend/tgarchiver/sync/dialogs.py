"""Dialogs, Telegram folders (DialogFilter) and forum topics."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from telethon import utils
from telethon.tl import types
from telethon.tl.functions.messages import GetDialogFiltersRequest, GetForumTopicsRequest

from tgarchiver.core.db import now_iso
from tgarchiver.jobs.engine import FloodWait, JobContext
from tgarchiver.sync.normalize import chat_row

if TYPE_CHECKING:
    from tgarchiver.services import Services

log = logging.getLogger(__name__)

CHAT_COLS = ("id", "account_id", "access_hash", "type", "title", "username", "is_forum", "is_archived",
             "unread_count", "unread_mentions", "read_inbox_max_id", "last_message_id", "last_message_at",
             "noforwards", "phone", "is_contact")


def input_peer(row: dict[str, Any]) -> Any:
    """Rebuild an InputPeer from stored id/access_hash (StringSession keeps no entity cache across restarts)."""
    real_id, peer_cls = utils.resolve_id(int(row["id"]))
    if row["type"] == "saved":
        return types.InputPeerSelf()
    if peer_cls is types.PeerUser:
        return types.InputPeerUser(real_id, int(row["access_hash"] or 0))
    if peer_cls is types.PeerChat:
        return types.InputPeerChat(real_id)
    return types.InputPeerChannel(real_id, int(row["access_hash"] or 0))


async def upsert_chats(svc: Services, rows: list[dict[str, Any]]) -> None:
    cols = ",".join(CHAT_COLS)
    ph = ",".join("?" * len(CHAT_COLS))
    upd = ",".join(f"{c}=excluded.{c}" for c in CHAT_COLS if c != "id")
    async with svc.db.tx() as c:
        await c.executemany(
            f"INSERT INTO chats({cols}, updated_at) VALUES({ph}, ?) ON CONFLICT(id) DO UPDATE SET {upd}, "
            "updated_at=excluded.updated_at",
            [tuple(r[k] for k in CHAT_COLS) + (now_iso(),) for r in rows],
        )


async def sync_dialogs(svc: Services, ctx: JobContext) -> dict[str, Any]:
    client = await svc.tg.authorized_client()
    me = svc.tg.me or await svc.tg.load_me() or {}
    me_id = me.get("id")
    rows: list[dict[str, Any]] = []
    forums: list[dict[str, Any]] = []
    await ctx.progress(stage="dialogs", done=0, force=True)

    async def collect() -> None:
        async for d in client.iter_dialogs(archived=None):
            r = chat_row(d, me_id)
            rows.append(r)
            if r["is_forum"]:
                forums.append(r)
            if len(rows) % 100 == 0:
                await ctx.progress(done=len(rows))
                await ctx.check()

    await svc.tg.call(collect)
    # iter_dialogs(archived=None) may omit the archive flag; mark archived explicitly.
    archived_ids: set[int] = set()

    async def collect_archived() -> None:
        async for d in client.iter_dialogs(archived=True):
            archived_ids.add(utils.get_peer_id(d.entity))

    await svc.tg.call(collect_archived)
    for r in rows:
        if r["id"] in archived_ids:
            r["is_archived"] = 1
    await upsert_chats(svc, rows)
    await ctx.progress(done=len(rows), total=len(rows), stage="topics", force=True)

    for f in forums:
        await ctx.check()
        try:
            await sync_topics(svc, f)
        except FloodWait:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("topics for %s failed: %s", f["id"], type(e).__name__)

    if svc.settings.import_tg_folders:
        await ctx.progress(stage="folders", force=True)
        await import_tg_folders(svc)
    svc.bus.emit("chats.changed")
    return {"chats": len(rows)}


async def sync_topics(svc: Services, chat: dict[str, Any]) -> int:
    client = await svc.tg.authorized_client()
    peer = input_peer(chat)
    offset_date, offset_id, offset_topic = None, 0, 0
    total = 0
    while True:
        req = GetForumTopicsRequest(peer=peer, offset_date=offset_date, offset_id=offset_id, offset_topic=offset_topic,
                                    limit=100)
        res = await svc.tg.call(lambda req=req: client(req))  # type: ignore[misc]
        topics = [t for t in res.topics if isinstance(t, types.ForumTopic)]
        if not topics:
            break
        async with svc.db.tx() as c:
            await c.executemany(
                "INSERT INTO topics(id, chat_id, title, icon, top_message, unread_count) VALUES(?,?,?,?,?,?) "
                "ON CONFLICT(chat_id, id) DO UPDATE SET title=excluded.title, icon=excluded.icon, "
                "top_message=excluded.top_message, unread_count=excluded.unread_count",
                [(t.id, chat["id"], t.title, str(t.icon_emoji_id or ""), t.top_message, t.unread_count)
                 for t in topics],
            )
        total += len(topics)
        if len(res.topics) < 100:
            break
        last = res.topics[-1]
        if last.id == offset_topic:
            break  # no progress - avoid looping on the same page
        offset_topic = last.id
        offset_id = getattr(last, "top_message", 0)
        # Pages are keyed by the date of the topic's top message, not the topic creation date.
        top = next((m for m in getattr(res, "messages", []) if getattr(m, "id", None) == offset_id), None)
        offset_date = getattr(top, "date", None) or getattr(last, "date", None)
    return total


def _filter_match(f: types.DialogFilter, chat: dict[str, Any]) -> bool:
    t = chat["type"]
    if f.exclude_archived and chat["is_archived"]:
        return False
    if f.exclude_read and not chat["unread_count"]:
        return False
    if t == "user" and ((f.contacts and chat["is_contact"]) or (f.non_contacts and not chat["is_contact"])):
        return True
    if t in ("group", "supergroup", "forum") and f.groups:
        return True
    if t == "channel" and f.broadcasts:
        return True
    return bool(t == "bot" and f.bots)


async def import_tg_folders(svc: Services) -> int:
    client = await svc.tg.authorized_client()
    res = await svc.tg.call(lambda: client(GetDialogFiltersRequest()))
    filters = getattr(res, "filters", res)
    chats = await svc.db.fetchall("SELECT id, type, is_archived, unread_count, is_contact FROM chats")
    count = 0
    for pos, f in enumerate(filters):
        if not isinstance(f, (types.DialogFilter, types.DialogFilterChatlist)):
            continue
        title = f.title.text if hasattr(f.title, "text") else str(f.title)
        include = {utils.get_peer_id(p) for p in (*f.pinned_peers, *f.include_peers) if not isinstance(p, types.InputPeerSelf)}
        exclude = {utils.get_peer_id(p) for p in getattr(f, "exclude_peers", []) or []}
        members = set(include)
        if isinstance(f, types.DialogFilter):
            members |= {c["id"] for c in chats if _filter_match(f, c)}
        members -= exclude
        existing = await svc.db.fetchone("SELECT id FROM folders WHERE source='telegram' AND tg_filter_id=?", (f.id,))
        if existing:
            folder_id = existing["id"]
            await svc.db.execute("UPDATE folders SET name=?, emoji=?, position=? WHERE id=?",
                                 (title, getattr(f, "emoticon", None), pos, folder_id))
        else:
            folder_id = await svc.db.execute(
                "INSERT INTO folders(name, color, emoji, parent_id, source, tg_filter_id, position) "
                "VALUES(?,?,?,NULL,'telegram',?,?)", (title, None, getattr(f, "emoticon", None), f.id, pos))
        async with svc.db.tx() as c:
            await c.execute("DELETE FROM folder_chats WHERE folder_id=?", (folder_id,))
            await c.executemany("INSERT OR IGNORE INTO folder_chats(folder_id, chat_id) VALUES(?,?)",
                                [(folder_id, cid) for cid in members])
        count += 1
    svc.bus.emit("folders.changed")
    return count
