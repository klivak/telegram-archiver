"""Persistent job engine: queued|running|paused|flood_wait|failed|done|cancelled, checkpoints in the DB.

Handlers are ``async def handler(ctx: JobContext)``. They must call ``await ctx.check()`` regularly so pause/cancel
takes effect, persist resume points with ``ctx.save_checkpoint``, and raise ``FloodWait`` (never sleep it away)
when Telegram asks to wait.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from tgarchiver.core.db import Database, dumps, loads, now_iso
from tgarchiver.core.events import EventBus

log = logging.getLogger(__name__)

ACTIVE = ("queued", "running", "flood_wait")
FINAL = ("done", "failed", "cancelled")


class FloodWait(Exception):  # noqa: N818
    def __init__(self, seconds: int, what: str = "") -> None:
        super().__init__(f"flood wait {seconds}s {what}".strip())
        self.seconds = int(seconds)
        self.what = what


class Deferred(Exception):  # noqa: N818
    """Job wants to continue later (download window, daily limit, disk full) - not a Telegram flood wait."""

    def __init__(self, seconds: int, reason: str) -> None:
        super().__init__(reason)
        self.seconds = int(seconds)
        self.reason = reason


class RetryableError(Exception):
    """Transient failure (network etc.) - retried with exponential backoff."""


class JobPaused(Exception):  # noqa: N818
    pass


class JobCancelled(Exception):  # noqa: N818
    pass


class JobContext:
    def __init__(self, engine: JobEngine, job: dict[str, Any]) -> None:
        self.engine = engine
        self.id: int = job["id"]
        self.kind: str = job["kind"]
        self.params: dict[str, Any] = loads(job["params"], {}) or {}
        self.checkpoint: dict[str, Any] = loads(job["checkpoint"], {}) or {}
        self.progress_data: dict[str, Any] = loads(job["progress"], {}) or {}
        self._pause = False
        self._cancel = False
        self._last_persist = 0.0

    @property
    def db(self) -> Database:
        return self.engine.db

    async def check(self) -> None:
        if self._cancel:
            raise JobCancelled()
        if self._pause:
            raise JobPaused()

    async def progress(self, *, force: bool = False, **fields: Any) -> None:
        self.progress_data.update(fields)
        now = time.monotonic()
        if force or now - self._last_persist >= 1.0:
            self._last_persist = now
            await self.db.execute(
                "UPDATE jobs SET progress=?, updated_at=? WHERE id=?", (dumps(self.progress_data), now_iso(), self.id)
            )
        self.engine.bus.emit(
            "job.progress", {"id": self.id, "progress": self.progress_data}, throttle_key=f"job:{self.id}"
        )

    async def save_checkpoint(self, **fields: Any) -> None:
        """Persist a resume point. Progress was made, so the transient-error budget starts over."""
        self.checkpoint.update(fields)
        await self.db.execute(
            "UPDATE jobs SET checkpoint=?, progress=?, attempts=0, updated_at=? WHERE id=?",
            (dumps(self.checkpoint), dumps(self.progress_data), now_iso(), self.id),
        )

    async def sub_job(self, kind: str, params: dict[str, Any], title: str = "") -> int:
        return await self.engine.submit(kind, params, title=title, parent_id=self.id)


Handler = Callable[[JobContext], Awaitable[Any]]


class JobEngine:
    def __init__(self, db: Database, bus: EventBus, *, max_concurrent: int = 3, max_retries: int = 5) -> None:
        self.db = db
        self.bus = bus
        self.max_concurrent = max_concurrent
        self.max_retries = max_retries
        self.handlers: dict[str, Handler] = {}
        self.exclusive: dict[str, str] = {}  # kind -> lane (one running job per lane)
        self.running: dict[int, tuple[asyncio.Task[Any], JobContext]] = {}
        self._wake = asyncio.Event()
        self._loop_task: asyncio.Task[Any] | None = None
        self._stopping = False

    def register(self, kind: str, handler: Handler, *, lane: str | None = None) -> None:
        self.handlers[kind] = handler
        if lane:
            self.exclusive[kind] = lane

    # ---------- lifecycle ----------
    async def start(self) -> None:
        # Anything that was running when the process died goes back to the queue; checkpoints make it resume.
        await self.db.execute("UPDATE jobs SET status='queued' WHERE status='running'")
        self._stopping = False
        self._loop_task = asyncio.create_task(self._loop(), name="job-engine")

    async def stop(self) -> None:
        self._stopping = True
        self._wake.set()
        for task, ctx in list(self.running.values()):
            ctx._pause = True
            task.cancel()
        for task, _ in list(self.running.values()):
            try:
                await task
            except BaseException:  # noqa: BLE001
                pass
        # Interrupted by shutdown: keep them queued to resume on next start.
        await self.db.execute("UPDATE jobs SET status='queued' WHERE status='running'")
        if self._loop_task:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except BaseException:  # noqa: BLE001
                pass

    # ---------- API ----------
    async def submit(self, kind: str, params: dict[str, Any] | None = None, *, title: str = "",
                     parent_id: int | None = None) -> int:
        if kind not in self.handlers:
            raise ValueError(f"unknown job kind: {kind}")
        ts = now_iso()
        job_id = await self.db.execute(
            "INSERT INTO jobs(kind, title, params, status, progress, checkpoint, attempts, parent_id, created_at, "
            "updated_at) VALUES(?,?,?,?,?,?,0,?,?,?)",
            (kind, title or kind, dumps(params or {}), "queued", dumps({}), dumps({}), parent_id, ts, ts),
        )
        await self._emit_job(job_id)
        self._wake.set()
        return job_id

    async def get(self, job_id: int) -> dict[str, Any] | None:
        row = await self.db.fetchone("SELECT * FROM jobs WHERE id=?", (job_id,))
        return _public(row) if row else None

    async def list_jobs(self, *, active_only: bool = False, limit: int = 200) -> list[dict[str, Any]]:
        if active_only:
            rows = await self.db.fetchall(
                "SELECT * FROM jobs WHERE status NOT IN ('done','failed','cancelled') ORDER BY id DESC LIMIT ?",
                (limit,),
            )
        else:
            rows = await self.db.fetchall("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,))
        return [_public(r) for r in rows]

    async def pause(self, job_id: int) -> None:
        if job_id in self.running:
            self.running[job_id][1]._pause = True
        else:
            await self._set_status(job_id, "paused", only_from=("queued", "flood_wait"))
        for child in await self._children(job_id):
            await self.pause(child)

    async def resume(self, job_id: int) -> None:
        await self._set_status(job_id, "queued", only_from=("paused",))
        for child in await self._children(job_id):
            await self.resume(child)
        self._wake.set()

    async def cancel(self, job_id: int) -> None:
        if job_id in self.running:
            self.running[job_id][1]._cancel = True
        else:
            await self._set_status(job_id, "cancelled", only_from=("queued", "paused", "flood_wait", "failed"))
        for child in await self._children(job_id):
            await self.cancel(child)

    async def retry(self, job_id: int) -> None:
        await self.db.execute(
            "UPDATE jobs SET status='queued', attempts=0, error=NULL, wait_until=NULL, updated_at=? "
            "WHERE id=? AND status IN ('failed','cancelled')",
            (now_iso(), job_id),
        )
        await self._emit_job(job_id)
        self._wake.set()

    async def pause_all(self) -> None:
        for job_id in list(self.running):
            self.running[job_id][1]._pause = True
        await self.db.execute(
            "UPDATE jobs SET status='paused', updated_at=? WHERE status IN ('queued','flood_wait')", (now_iso(),)
        )
        self.bus.emit("jobs.changed")

    async def resume_all(self) -> None:
        await self.db.execute("UPDATE jobs SET status='queued', updated_at=? WHERE status='paused'", (now_iso(),))
        self.bus.emit("jobs.changed")
        self._wake.set()

    async def clear_finished(self) -> None:
        await self.db.execute("DELETE FROM jobs WHERE status IN ('done','cancelled')")
        self.bus.emit("jobs.changed")

    async def wait(self, job_id: int, timeout: float = 30.0) -> dict[str, Any]:
        """Test helper: wait until a job reaches a final/paused state."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            job = await self.get(job_id)
            if job and job["status"] in (*FINAL, "paused", "flood_wait"):
                if job_id not in self.running:
                    return job
            await asyncio.sleep(0.02)
        raise TimeoutError(f"job {job_id} did not finish")

    # ---------- internals ----------
    async def _children(self, job_id: int) -> list[int]:
        rows = await self.db.fetchall("SELECT id FROM jobs WHERE parent_id=?", (job_id,))
        return [r["id"] for r in rows]

    async def _set_status(self, job_id: int, status: str, *, only_from: tuple[str, ...] | None = None,
                          **extra: Any) -> None:
        sets = ["status=?", "updated_at=?"]
        vals: list[Any] = [status, now_iso()]
        for k, v in extra.items():
            sets.append(f"{k}=?")
            vals.append(v)
        sql = f"UPDATE jobs SET {', '.join(sets)} WHERE id=?"
        vals.append(job_id)
        if only_from:
            sql += f" AND status IN ({','.join('?' * len(only_from))})"
            vals.extend(only_from)
        await self.db.execute(sql, vals)
        await self._emit_job(job_id)

    async def _emit_job(self, job_id: int) -> None:
        job = await self.get(job_id)
        if job:
            self.bus.emit("job.update", job)

    async def _loop(self) -> None:
        while not self._stopping:
            try:
                await self._tick()
            except Exception:  # noqa: BLE001
                log.exception("job loop tick failed")
            self._wake.clear()
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=1.0)
            except TimeoutError:
                pass

    async def _tick(self) -> None:
        now = datetime.now(UTC).isoformat(timespec="seconds")
        # Flood waits that have elapsed go back to the queue automatically.
        rows = await self.db.fetchall(
            "SELECT id FROM jobs WHERE status='flood_wait' AND (wait_until IS NULL OR wait_until<=?)", (now,)
        )
        for r in rows:
            await self._set_status(r["id"], "queued", only_from=("flood_wait",))
        free = self.max_concurrent - len(self.running)
        if free <= 0:
            return
        busy_lanes = {self.exclusive.get(ctx.kind) for _, ctx in self.running.values()} - {None}
        candidates = await self.db.fetchall(
            "SELECT * FROM jobs WHERE status='queued' AND (wait_until IS NULL OR wait_until<=?) ORDER BY id LIMIT 50",
            (now,),
        )
        for job in candidates:
            if free <= 0:
                break
            if job["id"] in self.running or job["kind"] not in self.handlers:
                continue
            lane = self.exclusive.get(job["kind"])
            if lane and lane in busy_lanes:
                continue
            if lane:
                busy_lanes.add(lane)
            free -= 1
            await self._launch(job)

    async def _launch(self, job: dict[str, Any]) -> None:
        # Atomic claim: a pause/cancel that landed after _tick read the row wins.
        claimed = await self.db.execute(
            "UPDATE jobs SET status='running', wait_until=NULL, updated_at=? WHERE id=? AND status='queued'",
            (now_iso(), job["id"]))
        if not claimed:
            return
        ctx = JobContext(self, job)
        for k in ("flood_wait", "flood_until", "flood_what", "deferred", "deferred_until"):
            ctx.progress_data.pop(k, None)
        task = asyncio.create_task(self._run(ctx), name=f"job-{job['id']}")
        self.running[job["id"]] = (task, ctx)
        await self._emit_job(job["id"])

    async def _run(self, ctx: JobContext) -> None:
        handler = self.handlers[ctx.kind]
        try:
            try:
                result = await handler(ctx)
                if result is not None:
                    ctx.progress_data["result"] = result
                await ctx.progress(force=True)
                await self._set_status(ctx.id, "done", error=None)
            except JobPaused:
                await ctx.progress(force=True)
                await self._set_status(ctx.id, "paused")
            except JobCancelled:
                await ctx.progress(force=True)
                await self._set_status(ctx.id, "cancelled")
            except FloodWait as fw:
                until = datetime.now(UTC) + timedelta(seconds=fw.seconds + 1)
                await ctx.progress(force=True, flood_wait=fw.seconds, flood_until=until.isoformat(), flood_what=fw.what)
                await self._set_status(ctx.id, "flood_wait", wait_until=until.isoformat(timespec="seconds"))
                self.bus.emit("flood_wait", {"job_id": ctx.id, "seconds": fw.seconds, "until": until.isoformat()})
            except Deferred as d:
                resume_at = (datetime.now(UTC) + timedelta(seconds=d.seconds)).isoformat(timespec="seconds")
                await ctx.progress(force=True, deferred=d.reason, deferred_until=resume_at)
                await self._set_status(ctx.id, "queued", wait_until=resume_at)
            except asyncio.CancelledError:
                if not self._stopping:
                    await self._set_status(ctx.id, "paused")
                raise
            except (RetryableError, ConnectionError, OSError, TimeoutError) as e:
                await self._on_transient(ctx, e)
            except Exception as e:  # noqa: BLE001
                log.exception("job %s (%s) failed", ctx.id, ctx.kind)
                await self._set_status(ctx.id, "failed", error=f"{type(e).__name__}: {e}"[:500])
        finally:
            self.running.pop(ctx.id, None)
            self._wake.set()

    async def _on_transient(self, ctx: JobContext, e: BaseException) -> None:
        row = await self.db.fetchone("SELECT attempts FROM jobs WHERE id=?", (ctx.id,))
        attempts = (row["attempts"] if row else 0) + 1
        err = f"{type(e).__name__}: {e}"[:500]
        if attempts > self.max_retries:
            await self._set_status(ctx.id, "failed", error=err, attempts=attempts)
            return
        delay = min(2 ** (attempts - 1), 300)
        until = (datetime.now(UTC) + timedelta(seconds=delay)).isoformat(timespec="seconds")
        log.warning("job %s transient error, retry %s in %ss: %s", ctx.id, attempts, delay, type(e).__name__)
        await self._set_status(ctx.id, "queued", error=err, attempts=attempts, wait_until=until)


def _public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "title": row["title"],
        "params": loads(row["params"], {}),
        "status": row["status"],
        "progress": loads(row["progress"], {}),
        "error": row["error"],
        "attempts": row["attempts"],
        "wait_until": row["wait_until"],
        "parent_id": row["parent_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }
