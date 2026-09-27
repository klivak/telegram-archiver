"""Local transcription with faster-whisper (optional extra `[whisper]`), run in a separate process (docs/09)."""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tgarchiver.core.db import now_iso
from tgarchiver.core.paths import local_data_dir
from tgarchiver.jobs.engine import JobContext
from tgarchiver.media.downloader import MediaQueue, chat_dir

if TYPE_CHECKING:
    from tgarchiver.services import Services

log = logging.getLogger(__name__)

AUDIO_TYPES = ("voice", "round", "audio", "video")
IDLE_UNLOAD_S = 300

_MODEL: Any = None
_MODEL_KEY: tuple[str, str, str] | None = None


def available() -> bool:
    return importlib.util.find_spec("faster_whisper") is not None


def _worker_transcribe(path: str, model: str, device: str, compute_type: str, beam: int, language: str,
                       cache_dir: str) -> dict[str, Any]:
    """Runs in the worker process. The model stays loaded between calls until the pool is shut down."""
    global _MODEL, _MODEL_KEY
    from faster_whisper import WhisperModel  # heavy import, only in the worker

    def run(dev: str, ctype: str) -> dict[str, Any]:
        global _MODEL, _MODEL_KEY
        key = (model, dev, ctype)
        if _MODEL is None or _MODEL_KEY != key:
            _MODEL = WhisperModel(model, device=dev, compute_type=ctype, download_root=cache_dir)
            _MODEL_KEY = key
        segments, info = _MODEL.transcribe(path, beam_size=beam, language=None if language == "auto" else language,
                                           vad_filter=True)
        segs = [(float(s.start), float(s.end), s.text.strip()) for s in segments]
        return {"lang": info.language, "segments": segs, "device": dev}

    try:
        return run(device, compute_type)
    except (RuntimeError, OSError) as e:
        # GPU present but CUDA/cuBLAS/cuDNN libraries missing (common on Windows): fall back to the CPU
        msg = str(e).lower()
        if device == "cpu" or not any(k in msg for k in ("cuda", "cublas", "cudnn", "dll", "library")):
            raise
        _MODEL = None
        return run("cpu", "int8")


def status() -> dict[str, Any]:
    """Module installed, CUDA devices seen by CTranslate2, and models already downloaded (no model is loaded)."""
    out: dict[str, Any] = {"installed": available(), "cuda_devices": 0, "models": []}
    if out["installed"]:
        try:
            import ctranslate2

            out["cuda_devices"] = int(ctranslate2.get_cuda_device_count())
        except Exception:  # noqa: BLE001
            pass
    cache = local_data_dir() / "whisper-models"
    if cache.exists():
        out["models"] = sorted(p.name.removeprefix("models--Systran--faster-whisper-") for p in cache.iterdir()
                               if p.is_dir() and p.name.startswith("models--"))
    return out


def srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_srt(segments: list[tuple[float, float, str]]) -> str:
    return "\n".join(f"{i}\n{srt_time(a)} --> {srt_time(b)}\n{t}\n" for i, (a, b, t) in enumerate(segments, 1))


class WhisperRunner:
    def __init__(self) -> None:
        self.pool: ProcessPoolExecutor | None = None
        self.last_used = 0.0
        self._lock = asyncio.Lock()  # one transcription at a time (GPU/CPU bound)
        self._reaper: asyncio.Task[Any] | None = None

    async def transcribe(self, path: Path, s: Any) -> dict[str, Any]:
        if not available():
            raise RuntimeError("whisper_not_installed")
        async with self._lock:
            if self.pool is None:
                self.pool = ProcessPoolExecutor(max_workers=1)
            self.last_used = time.monotonic()
            loop = asyncio.get_running_loop()
            cache = str(local_data_dir() / "whisper-models")
            device = "auto" if s.device == "auto" else s.device
            ctype = s.compute_type if s.compute_type != "auto" else "default"
            res = await loop.run_in_executor(self.pool, _worker_transcribe, str(path), s.model, device, ctype,
                                             s.beam_size, s.language, cache)
            self.last_used = time.monotonic()
            if self._reaper is None or self._reaper.done():
                self._reaper = asyncio.create_task(self._reap())
            return res

    async def _reap(self) -> None:
        while self.pool is not None:
            await asyncio.sleep(30)
            if time.monotonic() - self.last_used > IDLE_UNLOAD_S and not self._lock.locked():
                await self.stop()

    async def stop(self) -> None:
        if self.pool is not None:
            self.pool.shutdown(wait=False, cancel_futures=True)
            self.pool = None


async def write_chat_transcripts_md(svc: Services, chat_id: int) -> Path:
    chat = await svc.db.fetchone("SELECT * FROM chats WHERE id=?", (chat_id,))
    assert chat
    out = chat_dir(svc, chat) / "transcripts.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(f"# {chat['title']} - transcripts\n\n")
        sql = ("SELECT m.id, m.date, u.first_name, u.last_name, u.username, m.out, t.text, t.lang FROM transcripts t "
               "JOIN media d ON d.id=t.media_id JOIN messages m ON m.chat_id=d.chat_id AND m.id=d.message_id "
               "LEFT JOIN users u ON u.id=m.sender_id WHERE d.chat_id=? ORDER BY m.id")
        async for batch in svc.db.iterate(sql, (chat_id,)):
            for r in batch:
                who = " ".join(p for p in (r["first_name"], r["last_name"]) if p) or r["username"] or "?"
                fh.write(f"**{(r['date'] or '')[:16].replace('T', ' ')} · {who}** ({r['lang']})\n\n{r['text']}\n\n")
    return out


async def transcribe_job(svc: Services, ctx: JobContext, params: dict[str, Any] | None = None) -> dict[str, Any]:
    runner: WhisperRunner = svc.whisper
    p = ctx.params if params is None else params
    where = ["d.type IN ('voice','round','audio','video')", "t.media_id IS NULL"]
    args: list[Any] = []
    if p.get("media_ids"):
        ids = [int(i) for i in p["media_ids"]]
        where.append(f"d.id IN ({','.join('?' * len(ids))})")
        args.extend(ids)
    if p.get("chat_id"):
        where.append("d.chat_id=?")
        args.append(int(p["chat_id"]))
    if not p.get("include_video", False) and not p.get("media_ids"):
        where.append("d.type != 'video'")
    rows = await svc.db.fetchall(
        f"SELECT d.* FROM media d LEFT JOIN transcripts t ON t.media_id=d.id WHERE {' AND '.join(where)} "
        f"ORDER BY d.id", args)
    # Make sure the audio is on disk first (high priority download inside this job).
    missing = [r["id"] for r in rows if r["status"] != "done"]
    if missing:
        await svc.db.execute(
            f"UPDATE media SET status='pending', priority=50, attempts=0 WHERE id IN ({','.join('?' * len(missing))}) "
            f"AND status != 'done'", missing)
        await ctx.progress(stage="download", force=True)
        q = MediaQueue(svc, ctx)
        q.chat_ids = sorted({r["chat_id"] for r in rows})
        await q.run()
    done = 0
    chats: set[int] = set()
    for i, r in enumerate(rows):
        await ctx.check()
        row = await svc.db.fetchone("SELECT * FROM media WHERE id=?", (r["id"],))
        if not row or row["status"] != "done" or not row["path"] or not Path(row["path"]).exists():
            continue
        await ctx.progress(stage="transcribe", done=i, total=len(rows), current=row["file_name"], force=True)
        res = await runner.transcribe(Path(row["path"]), svc.settings.whisper)
        text = " ".join(t for _, _, t in res["segments"]).strip()
        base = Path(row["path"])
        txt, srt = base.with_suffix(base.suffix + ".txt"), base.with_suffix(base.suffix + ".srt")
        txt.write_text(text + "\n", encoding="utf-8")
        srt.write_text(to_srt(res["segments"]), encoding="utf-8")
        await svc.db.execute(
            "INSERT OR REPLACE INTO transcripts(media_id, lang, model, text, txt_path, srt_path, created_at) "
            "VALUES(?,?,?,?,?,?,?)", (row["id"], res["lang"], svc.settings.whisper.model, text, str(txt), str(srt),
                                      now_iso()))
        svc.bus.emit("transcript.done", {"media_id": row["id"], "chat_id": row["chat_id"], "device": res.get("device")})
        chats.add(row["chat_id"])
        done += 1
    for cid in chats:
        await write_chat_transcripts_md(svc, cid)
    await ctx.progress(done=len(rows), total=len(rows), force=True)
    return {"transcribed": done}
