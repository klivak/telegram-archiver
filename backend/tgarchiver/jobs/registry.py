"""Registers every job kind with the engine."""

from __future__ import annotations

import asyncio
from functools import partial
from typing import TYPE_CHECKING, Any

from tgarchiver.jobs.engine import JobContext

if TYPE_CHECKING:
    from tgarchiver.services import Services


async def demo_job(ctx: JobContext) -> dict[str, Any]:
    """M0 test job: counts with checkpoints so pause/resume/cancel can be exercised without Telegram."""
    total = int(ctx.params.get("steps", 20))
    delay = float(ctx.params.get("delay", 0.25))
    start = int(ctx.checkpoint.get("step", 0))
    for step in range(start, total):
        await ctx.check()
        await asyncio.sleep(delay)
        ctx.progress_data.update(done=step + 1, total=total)
        await ctx.save_checkpoint(step=step + 1)
        await ctx.progress()
    return {"steps": total}


def register_all(svc: Services) -> None:
    from tgarchiver.ai.pipeline import ai_job
    from tgarchiver.export.job import export_job, render_job
    from tgarchiver.media.downloader import download_media_job
    from tgarchiver.miniapps.recorder import replay_har, run_session
    from tgarchiver.miniapps.service import detect_job
    from tgarchiver.monitor.service import monitor_job
    from tgarchiver.sync.dialogs import sync_dialogs
    from tgarchiver.sync.history import chat_preview_job, sync_history_job
    from tgarchiver.transcribe.whisper import transcribe_job

    e = svc.engine
    e.register("demo", demo_job)

    def bind(fn: Any) -> Any:
        return partial(fn, svc)

    # Telegram-bound jobs share one "tg" lane where parallel runs would only fight over the rate limiter.
    e.register("sync_dialogs", bind(sync_dialogs), lane="dialogs")
    e.register("sync_history", bind(sync_history_job), lane="history")
    e.register("chat_preview", bind(chat_preview_job), lane="preview")
    e.register("export", bind(export_job), lane="history")
    e.register("render", bind(render_job))
    e.register("download_media", bind(download_media_job), lane="media")
    e.register("transcribe", bind(transcribe_job), lane="whisper")
    e.register("detect_miniapps", bind(detect_job), lane="dialogs")
    e.register("miniapp_session", bind(run_session), lane="browser")
    e.register("miniapp_replay", bind(replay_har), lane="browser")
    e.register("monitor", bind(monitor_job), lane="monitor")
    e.register("ai", bind(ai_job), lane="ai")
