"""The `export` job: per chat sync history (media downloads start in parallel) -> render files; then finish media."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import AsyncExitStack
from typing import TYPE_CHECKING, Any

from telethon import errors

from tgarchiver.export.exporter import Exporter, export_full_file
from tgarchiver.jobs.engine import FloodWait, JobCancelled, JobContext
from tgarchiver.media.downloader import MediaQueue, enqueue
from tgarchiver.sync.history import get_chat, migrated_from_chat, sync_chat_history

if TYPE_CHECKING:
    from tgarchiver.services import Services

log = logging.getLogger(__name__)


async def export_job(svc: Services, ctx: JobContext) -> dict[str, Any]:
    try:
        return await _export(svc, ctx)
    except JobCancelled:
        # cancelling the export also takes its not-yet-started files out of the queue
        p = ctx.params
        chat_ids = [int(c) for c in p.get("chat_ids", [])]
        types_ = [] if p.get("no_media") else list(p.get("media_types") or [])
        if chat_ids and types_:
            await svc.db.execute(
                f"UPDATE media SET status='available' WHERE status='pending' AND priority=0 "
                f"AND chat_id IN ({','.join('?' * len(chat_ids))}) AND type IN ({','.join('?' * len(types_))})",
                [*chat_ids, *types_])
            svc.bus.emit("media.changed")
        raise


async def _export(svc: Services, ctx: JobContext) -> dict[str, Any]:
    p = ctx.params
    chat_ids: list[int] = [int(c) for c in (ctx.checkpoint.get("chat_ids") or p.get("chat_ids", []))]
    formats: list[str] = p.get("formats") or [p.get("format") or "md"]
    split: dict[str, Any] = p.get("split") or {"mode": "month"}
    media_types: list[str] = [] if p.get("no_media") else list(p.get("media_types") or [])
    filters: dict[str, Any] = p.get("filters") or {}
    opts = {"include_transcripts": p.get("include_transcripts", True),
            "date_from": filters.get("date_from"), "date_to": filters.get("date_to"), "topic_id": p.get("topic_id")}
    phase = ctx.checkpoint.get("phase", "chats")
    start = int(ctx.checkpoint.get("index", 0))
    skipped: list[int] = list(ctx.checkpoint.get("skipped", []))

    if phase == "chats":
        async with AsyncExitStack() as stack:
            client = await svc.tg.authorized_client()
            if p.get("takeout", svc.settings.use_takeout):
                try:
                    client = await stack.enter_async_context(client.takeout(
                        finalize=True, users=True, chats=True, megagroups=True, channels=True, files=True,
                        max_file_size=2000 * 1024 * 1024))
                except errors.TakeoutInitDelayError as e:
                    # Telegram asked to confirm the export in the "Telegram" service chat.
                    raise FloodWait(e.seconds, "takeout") from e
            i = start - 1
            while i + 1 < len(chat_ids):
                i += 1
                chat = await get_chat(svc, chat_ids[i])
                prefix = {"chats_done": i, "chats_total": len(chat_ids), "chat_title": chat["title"]}
                await ctx.progress(**prefix, stage="messages", force=True)
                if chat["noforwards"] and not svc.settings.protected_content:
                    skipped.append(chat["id"])
                    await ctx.save_checkpoint(index=i + 1, skipped=skipped)
                    continue
                media_task: asyncio.Task[Any] | None = None
                if media_types:
                    # files start downloading while the history is still coming in, not after it
                    new_batch = asyncio.Event()

                    async def queue_media(cid: int = chat["id"], ev: asyncio.Event = new_batch) -> None:
                        await enqueue(svc, [cid], media_types, filters)
                        ev.set()

                    media_task = asyncio.create_task(_media_worker(svc, ctx, chat["id"], new_batch, media_types))
                try:
                    await sync_chat_history(svc, ctx, chat["id"], client=client, progress_prefix=prefix,
                                            since=filters.get("date_from"),
                                            on_batch=queue_media if media_types else None)
                    if media_types:
                        await enqueue(svc, [chat["id"]], media_types, filters)
                finally:
                    if media_task:
                        media_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError, Exception):
                            await media_task
                old = await migrated_from_chat(svc, chat["id"])
                if old and old not in chat_ids:
                    chat_ids.insert(i + 1, old)  # the old group holds the history from before the upgrade
                    await ctx.save_checkpoint(chat_ids=chat_ids)
                await ctx.progress(stage="render", force=True)
                for fmt in formats:
                    await Exporter(svc, chat, fmt, split, opts).run(ctx)
                    if p.get("also_full") and split.get("mode") != "single":
                        await export_full_file(svc, chat, fmt, opts)
                await ctx.save_checkpoint(index=i + 1, skipped=skipped)
        await ctx.save_checkpoint(phase="media", index=len(chat_ids))
    await ctx.progress(chats_done=len(chat_ids), chats_total=len(chat_ids), force=True)

    media_result: dict[str, Any] = {}
    targets = [c for c in chat_ids if c not in skipped]
    if media_types and targets:
        await ctx.progress(stage="media", force=True)
        q = MediaQueue(svc, ctx)
        q.chat_ids = targets
        q.types = media_types
        media_result = await q.run()
    await ctx.progress(stage="done", force=True)
    svc.bus.emit("chats.changed")
    return {"chats": len(chat_ids), "skipped_protected": skipped, "media": media_result}


async def _media_worker(svc: Services, ctx: JobContext, chat_id: int, new_batch: asyncio.Event,
                        types_: list[str]) -> None:
    """Download queued files of one chat in the background while its history sync runs."""
    while True:
        await new_batch.wait()
        new_batch.clear()
        q = MediaQueue(svc, ctx)
        q.chat_ids = [chat_id]
        q.types = types_
        await q.run()


async def render_job(svc: Services, ctx: JobContext) -> dict[str, Any]:
    """Re-render exports from the local archive only (no network)."""
    p = ctx.params
    out = []
    for cid in p.get("chat_ids", []):
        chat = await get_chat(svc, int(cid))
        for fmt in p.get("formats") or ["md"]:
            out.append(await Exporter(svc, chat, fmt, p.get("split") or {"mode": "month"}, p.get("opts") or {}).run(ctx))
    return {"exports": out}
