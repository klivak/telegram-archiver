"""REST endpoints (docs/12). All require the Bearer token (see api.app)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ValidationError

from tgarchiver.core.db import dumps, loads, now_iso
from tgarchiver.services import Services
from tgarchiver.tg.auth import AuthError
from tgarchiver.tg.client import API_HASH_KEY, NotAuthorized

router = APIRouter()

# Archived/captured content is untrusted (other people's files, Mini App HTML). It is served from the app origin,
# so it must never run scripts there: CSP sandbox gives it an opaque origin without script execution.
UNTRUSTED_HEADERS = {"Content-Security-Policy": "sandbox", "X-Content-Type-Options": "nosniff"}


def untrusted_file(path: Path | str, media_type: str | None = None) -> FileResponse:
    return FileResponse(path, media_type=media_type, headers=UNTRUSTED_HEADERS)


def S(request: Request) -> Services:  # noqa: N802
    return request.app.state.svc  # type: ignore[no-any-return]


def _auth_error(e: AuthError) -> HTTPException:
    return HTTPException(400, {"code": e.code, "seconds": e.seconds})


# ------------------------------------------------------------------ auth
class ConfigIn(BaseModel):
    api_id: int
    api_hash: str = Field(min_length=32, max_length=32, pattern=r"^[0-9a-fA-F]{32}$")


@router.get("/auth/status")
async def auth_status(request: Request) -> dict[str, Any]:
    return await S(request).auth.status()


@router.post("/auth/config")
async def auth_config(request: Request, body: ConfigIn) -> dict[str, Any]:
    svc = S(request)
    await svc.auth.cancel_qr()  # the running QR loop belongs to the old client
    svc.settings.api_id = body.api_id
    svc.secrets.set(API_HASH_KEY, body.api_hash.lower())
    svc.save_settings()
    await svc.tg.reset()
    return await svc.auth.status()


@router.post("/auth/qr/start")
async def qr_start(request: Request) -> dict[str, Any]:
    try:
        return await S(request).auth.start_qr()
    except AuthError as e:
        raise _auth_error(e) from e
    except NotAuthorized as e:
        raise HTTPException(400, {"code": "need_config"}) from e


@router.post("/auth/qr/cancel")
async def qr_cancel(request: Request) -> dict[str, Any]:
    await S(request).auth.cancel_qr()
    return {"ok": True}


@router.post("/auth/phone/send")
async def phone_send(request: Request, phone: str = Body(..., embed=True)) -> dict[str, Any]:
    try:
        return await S(request).auth.send_code(phone.strip())
    except AuthError as e:
        raise _auth_error(e) from e


@router.post("/auth/phone/verify")
async def phone_verify(request: Request, code: str = Body(..., embed=True)) -> dict[str, Any]:
    try:
        return await S(request).auth.verify_code(code.strip())
    except AuthError as e:
        raise _auth_error(e) from e


@router.post("/auth/password")
async def auth_password(request: Request, password: str = Body(..., embed=True)) -> dict[str, Any]:
    try:
        return await S(request).auth.password(password)
    except AuthError as e:
        raise _auth_error(e) from e


@router.post("/auth/logout")
async def auth_logout(request: Request) -> dict[str, Any]:
    await S(request).auth.logout()
    return {"ok": True}


# ------------------------------------------------------------------ settings
SECRET_KEYS = {"ai_key_anthropic", "ai_key_openai", "ai_key_openrouter", "ai_key_groq", "ai_key_gemini"}


@router.get("/settings")
async def get_settings(request: Request) -> dict[str, Any]:
    svc = S(request)
    d = svc.settings.model_dump()
    d["secrets"] = {k: svc.secrets.has(k) for k in SECRET_KEYS}
    return d


@router.put("/settings")
async def put_settings(request: Request, patch: dict[str, Any] = Body(...)) -> dict[str, Any]:
    svc = S(request)
    patch.pop("api_id", None)
    patch.pop("secrets", None)
    merged = _deep_merge(svc.settings.model_dump(), patch)
    try:
        new = type(svc.settings).model_validate(merged)
    except ValidationError as e:
        raise HTTPException(422, [{"loc": err["loc"], "msg": err["msg"]} for err in e.errors()]) from e
    for k in type(svc.settings).model_fields:
        setattr(svc.settings, k, getattr(new, k))
    svc.tg.limiter.rate = max(svc.settings.history_rps, 0.05)
    svc.engine.max_retries = svc.settings.max_retries
    svc.save_settings()
    return await get_settings(request)


def _deep_merge(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    out = dict(a)
    for k, v in b.items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


@router.post("/settings/secret")
async def set_secret(request: Request, key: str = Body(...), value: str = Body("")) -> dict[str, Any]:
    if key not in SECRET_KEYS:
        raise HTTPException(400, "unknown secret")
    svc = S(request)
    if value:
        svc.secrets.set(key, value.strip())
    else:
        svc.secrets.delete(key)
    return {"ok": True, "set": bool(value)}


@router.get("/modules")
async def modules(request: Request) -> dict[str, Any]:
    import importlib.util

    from tgarchiver.miniapps.recorder import available as pw_ok
    from tgarchiver.transcribe.whisper import available as whisper_ok

    return {"whisper": whisper_ok(), "playwright": pw_ok(), "cryptg": importlib.util.find_spec("cryptg") is not None}


@router.get("/updates")
async def updates(request: Request, force: bool = False) -> dict[str, Any]:
    from tgarchiver.api.lifecycle import check_updates

    return await check_updates(S(request), force)


# ------------------------------------------------------------------ dashboard
@router.get("/dashboard")
async def dashboard(request: Request) -> dict[str, Any]:
    svc = S(request)
    db = svc.db
    stats = await db.fetchone(
        "SELECT (SELECT count(*) FROM chats) AS chats, (SELECT count(*) FROM chats WHERE stored_messages>0) AS "
        "archived_chats, (SELECT coalesce(sum(stored_messages),0) FROM chats) AS messages, "
        "(SELECT count(*) FROM media WHERE status='done') AS media_done, "
        "(SELECT coalesce(sum(size),0) FROM media WHERE status='done') AS media_bytes, "
        "(SELECT count(*) FROM media WHERE status IN ('pending','downloading')) AS media_pending, "
        "(SELECT coalesce(sum(unread_count),0) FROM chats) AS unread, "
        "(SELECT count(*) FROM chats WHERE unread_count>0) AS unread_chats, "
        "(SELECT count(*) FROM mini_apps) AS mini_apps, (SELECT count(*) FROM transcripts) AS transcripts")
    db_size = 0
    for suffix in ("", "-wal"):
        p = Path(db.path + suffix)
        if p.exists():
            db_size += p.stat().st_size
    return {
        "me": svc.tg.me,
        "archive_root": str(svc.settings.archive_path),
        "account_dir": str(svc.account_dir),
        "stats": {**(stats or {}), "db_bytes": db_size},
        "jobs": await svc.engine.list_jobs(active_only=True, limit=20),
        "last_report": await db.fetchone("SELECT id, kind, created_at FROM ai_reports ORDER BY id DESC LIMIT 1"),
    }


# ------------------------------------------------------------------ chats
CHAT_LIST_COLS = ("id, type, title, username, is_forum, is_archived, unread_count, unread_mentions, last_message_at, "
                  "noforwards, stored_messages, media_count, media_done, photo_path")


@router.get("/chats")
async def list_chats(request: Request, type: str | None = None, folder: int | None = None, q: str | None = None,
                     archived: int | None = None, unread: int | None = None, protected: int | None = None
                     ) -> dict[str, Any]:
    svc = S(request)
    where: list[str] = ["1=1"]
    args: list[Any] = []
    if type:
        types = type.split(",")
        where.append(f"c.type IN ({','.join('?' * len(types))})")
        args.extend(types)
    if folder:
        where.append("c.id IN (SELECT chat_id FROM folder_chats WHERE folder_id=?)")
        args.append(folder)
    if q:
        where.append("(c.title LIKE ? OR c.username LIKE ?)")
        args += [f"%{q}%", f"%{q}%"]
    if archived is not None:
        where.append("c.is_archived=?")
        args.append(archived)
    if unread:
        where.append("c.unread_count>0")
    if protected:
        where.append("c.noforwards=1")
    rows = await svc.db.fetchall(
        f"SELECT {CHAT_LIST_COLS}, (SELECT group_concat(folder_id) FROM folder_chats fc WHERE fc.chat_id=c.id) AS "
        f"folders, s.done AS synced, s.total AS total_messages FROM chats c LEFT JOIN sync_state s ON s.chat_id=c.id "
        f"AND s.topic_id=0 WHERE {' AND '.join(where)} ORDER BY c.last_message_at DESC", args)
    for r in rows:
        r["folders"] = [int(x) for x in r["folders"].split(",")] if r["folders"] else []
    return {"items": rows}


@router.post("/chats/refresh")
async def refresh_chats(request: Request) -> dict[str, Any]:
    return {"job_id": await S(request).engine.submit("sync_dialogs", {}, title="sync_dialogs")}


@router.get("/chats/{chat_id}")
async def get_chat(request: Request, chat_id: int) -> dict[str, Any]:
    svc = S(request)
    chat = await svc.db.fetchone("SELECT * FROM chats WHERE id=?", (chat_id,))
    if not chat:
        raise HTTPException(404)
    chat.pop("raw", None)
    chat.pop("access_hash", None)
    chat["topics"] = await svc.db.fetchall("SELECT * FROM topics WHERE chat_id=? ORDER BY id", (chat_id,))
    chat["sync"] = await svc.db.fetchone("SELECT * FROM sync_state WHERE chat_id=? AND topic_id=0", (chat_id,))
    chat["exports"] = await svc.db.fetchall("SELECT * FROM exports WHERE chat_id=?", (chat_id,))
    chat["media_by_type"] = await svc.db.fetchall(
        "SELECT type, count(*) AS n, sum(CASE WHEN status='done' THEN 1 ELSE 0 END) AS done, coalesce(sum(size),0) "
        "AS bytes FROM media WHERE chat_id=? GROUP BY type", (chat_id,))
    from tgarchiver.media.downloader import chat_dir

    chat["dir"] = str(chat_dir(svc, chat))
    return chat


@router.get("/chats/{chat_id}/messages")
async def chat_messages(request: Request, chat_id: int, before: int | None = None, after: int | None = None,
                        around: int | None = None, limit: int = 50, type: str | None = None,
                        topic: int | None = None, date: str | None = None) -> dict[str, Any]:
    svc = S(request)
    limit = max(1, min(limit, 200))
    where: list[str] = ["m.chat_id=?"]
    args: list[Any] = [chat_id]
    if type:
        types = type.split(",")
        where.append(f"m.media_type IN ({','.join('?' * len(types))})")
        args.extend(types)
    if topic is not None:
        where.append("m.topic_id=?")
        args.append(topic)
    if date:  # jump to date: first message on/after it
        first = await svc.db.scalar(f"SELECT min(m.id) FROM messages m WHERE {' AND '.join(where)} AND m.date>=?",
                                    [*args, date])
        around = first or around
    base = ("SELECT m.*, u.first_name, u.last_name, u.username, d.id AS media_id, d.type AS media_kind, d.file_name, "
            "d.size AS media_size, d.status AS media_status, d.mime, d.duration, t.text AS transcript "
            "FROM messages m LEFT JOIN users u ON u.id=m.sender_id LEFT JOIN media d ON d.chat_id=m.chat_id AND "
            "d.message_id=m.id LEFT JOIN transcripts t ON t.media_id=d.id")
    w = " AND ".join(where)
    if around is not None:
        older = await svc.db.fetchall(f"{base} WHERE {w} AND m.id<=? ORDER BY m.id DESC LIMIT ?",
                                      [*args, around, limit // 2])
        newer = await svc.db.fetchall(f"{base} WHERE {w} AND m.id>? ORDER BY m.id ASC LIMIT ?",
                                      [*args, around, limit // 2])
        items = list(reversed(older)) + newer
    elif after is not None:
        items = await svc.db.fetchall(f"{base} WHERE {w} AND m.id>? ORDER BY m.id ASC LIMIT ?", [*args, after, limit])
    else:
        cond = " AND m.id<?" if before is not None else ""
        extra = [before] if before is not None else []
        items = list(reversed(await svc.db.fetchall(f"{base} WHERE {w}{cond} ORDER BY m.id DESC LIMIT ?",
                                                    [*args, *extra, limit])))
    for it in items:
        for k in ("fwd_from", "reactions", "buttons", "raw"):
            it[k] = loads(it.get(k))
    return {"items": items}


@router.get("/chats/{chat_id}/avatar")
async def chat_avatar(request: Request, chat_id: int) -> Any:
    svc = S(request)
    chat = await svc.db.fetchone("SELECT * FROM chats WHERE id=?", (chat_id,))
    if not chat:
        raise HTTPException(404)
    path = svc.account_dir / "avatars" / f"{abs(chat_id)}.jpg"
    if chat["photo_path"] == "none":
        raise HTTPException(404)
    if not path.exists():
        from tgarchiver.sync.dialogs import input_peer

        try:
            client = await svc.tg.authorized_client()
            path.parent.mkdir(parents=True, exist_ok=True)
            got = await svc.tg.call(lambda: client.download_profile_photo(input_peer(chat), file=str(path),
                                                                         download_big=False))
        except Exception as e:  # noqa: BLE001  (network/flood: try again later, don't cache the miss)
            raise HTTPException(404) from e
        # Only a definite "no photo" answer is cached.
        await svc.db.execute("UPDATE chats SET photo_path=? WHERE id=?", (str(path) if got else "none", chat_id))
        if not got:
            raise HTTPException(404)
    return FileResponse(path, headers={"Cache-Control": "max-age=86400"})


# ------------------------------------------------------------------ export
class ExportIn(BaseModel):
    chat_ids: list[int]
    formats: list[str] = ["md"]
    split: dict[str, Any] = {"mode": "month"}
    media_types: list[str] = []
    no_media: bool = False
    filters: dict[str, Any] = {}
    also_full: bool = False
    include_transcripts: bool = True
    takeout: bool | None = None
    title: str = ""


@router.post("/export")
async def start_export(request: Request, body: ExportIn) -> dict[str, Any]:
    from tgarchiver.export.writers import FORMATS

    if not body.chat_ids:
        raise HTTPException(400, "no chats")
    if any(f not in FORMATS for f in body.formats):
        raise HTTPException(400, "bad format")
    svc = S(request)
    params = body.model_dump()
    if params["takeout"] is None:
        params["takeout"] = svc.settings.use_takeout
    title = body.title or ("export" if len(body.chat_ids) != 1 else "export_chat")
    job_id = await svc.engine.submit("export", params, title=title)
    return {"job_id": job_id}


@router.post("/export/estimate")
async def export_estimate(request: Request, body: ExportIn) -> dict[str, Any]:
    from tgarchiver.media.downloader import estimate

    svc = S(request)
    est = await estimate(svc, body.chat_ids, [] if body.no_media else body.media_types, body.filters)
    unsynced = await svc.db.scalar(
        f"SELECT count(*) FROM chats c LEFT JOIN sync_state s ON s.chat_id=c.id AND s.topic_id=0 WHERE c.id IN "
        f"({','.join('?' * len(body.chat_ids))}) AND coalesce(s.done,0)=0", body.chat_ids) if body.chat_ids else 0
    msgs = await svc.db.scalar(
        f"SELECT coalesce(sum(stored_messages),0) FROM chats WHERE id IN ({','.join('?' * len(body.chat_ids))})",
        body.chat_ids) if body.chat_ids else 0
    protected = await svc.db.scalar(
        f"SELECT count(*) FROM chats WHERE noforwards=1 AND id IN ({','.join('?' * len(body.chat_ids))})",
        body.chat_ids) if body.chat_ids else 0
    return {"media": est, "unsynced_chats": unsynced, "messages": msgs, "protected_chats": protected,
            "protected_enabled": svc.settings.protected_content}


@router.post("/open-path")
async def open_path(request: Request, path: str = Body(..., embed=True)) -> dict[str, Any]:
    svc = S(request)
    p = Path(path).resolve()
    root = svc.settings.archive_path.resolve()
    if p != root and root not in p.parents:
        raise HTTPException(403, "outside archive")
    if not p.exists():
        raise HTTPException(404)
    # Never "open" a file (that would execute downloaded .exe/.lnk/.bat); folders are opened, files revealed.
    if sys.platform == "win32":
        if p.is_dir():
            subprocess.Popen(["explorer", str(p)])  # noqa: S603, S607
        else:
            subprocess.Popen(["explorer", f"/select,{p}"])  # noqa: S603, S607
    else:
        target = p if p.is_dir() else p.parent
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(target)])  # noqa: S603, S607
    return {"ok": True}


@router.get("/archive-file")
async def archive_file(request: Request, path: str) -> Any:
    """Serve an exported file (e.g. HTML export preview) from inside the archive only."""
    svc = S(request)
    p = Path(path).resolve()
    root = svc.settings.archive_path.resolve()
    if root not in p.parents or not p.is_file():
        raise HTTPException(404)
    return untrusted_file(p)


# ------------------------------------------------------------------ jobs
@router.get("/jobs")
async def list_jobs(request: Request, active: bool = False, limit: int = 200) -> dict[str, Any]:
    return {"items": await S(request).engine.list_jobs(active_only=active, limit=limit)}


@router.get("/jobs/{job_id}")
async def get_job(request: Request, job_id: int) -> dict[str, Any]:
    job = await S(request).engine.get(job_id)
    if not job:
        raise HTTPException(404)
    return job


@router.post("/jobs/{job_id}/{action}")
async def job_action(request: Request, job_id: int, action: str) -> dict[str, Any]:
    e = S(request).engine
    fn = {"pause": e.pause, "resume": e.resume, "cancel": e.cancel, "retry": e.retry}.get(action)
    if fn is None:
        raise HTTPException(404)
    await fn(job_id)
    return {"ok": True}


@router.post("/jobs-all/{action}")
async def jobs_all(request: Request, action: str) -> dict[str, Any]:
    e = S(request).engine
    fn = {"pause": e.pause_all, "resume": e.resume_all, "clear": e.clear_finished}.get(action)
    if fn is None:
        raise HTTPException(404)
    await fn()
    return {"ok": True}


@router.post("/jobs-demo")
async def demo(request: Request, steps: int = Body(20, embed=True)) -> dict[str, Any]:
    return {"job_id": await S(request).engine.submit("demo", {"steps": steps, "delay": 0.3}, title="demo")}


# ------------------------------------------------------------------ media
async def _ensure_media_job(svc: Services) -> int:
    row = await svc.db.fetchone(
        "SELECT id, status FROM jobs WHERE kind='download_media' AND status IN ('queued','running','flood_wait','paused')"
        " ORDER BY id DESC LIMIT 1")
    if row:
        if row["status"] == "paused":
            await svc.engine.resume(row["id"])
        return int(row["id"])
    return await svc.engine.submit("download_media", {}, title="download_media")


@router.get("/media/queue")
async def media_queue(request: Request, status: str | None = None, chat_id: int | None = None, limit: int = 300,
                      offset: int = 0) -> dict[str, Any]:
    svc = S(request)
    where: list[str] = ["d.status NOT IN ('available')"]
    args: list[Any] = []
    if status:
        sts = status.split(",")
        where = [f"d.status IN ({','.join('?' * len(sts))})"]
        args.extend(sts)
    if chat_id:
        where.append("d.chat_id=?")
        args.append(chat_id)
    w = " AND ".join(where)
    items = await svc.db.fetchall(
        f"SELECT d.id, d.chat_id, d.message_id, d.type, d.file_name, d.size, d.bytes_done, d.status, d.priority, "
        f"d.attempts, d.error, d.updated_at, c.title AS chat_title FROM media d LEFT JOIN chats c ON c.id=d.chat_id "
        f"WHERE {w} ORDER BY CASE d.status WHEN 'downloading' THEN 0 WHEN 'pending' THEN 1 WHEN 'paused' THEN 2 "
        f"WHEN 'failed' THEN 3 ELSE 4 END, d.priority DESC, d.updated_at DESC LIMIT ? OFFSET ?", [*args, limit, offset])
    counts = await svc.db.fetchall("SELECT status, count(*) AS n, coalesce(sum(size),0) AS bytes, "
                                   "coalesce(sum(bytes_done),0) AS done FROM media GROUP BY status")
    return {"items": items, "counts": {r["status"]: r for r in counts}}


class MediaDownloadIn(BaseModel):
    chat_ids: list[int] = []
    media_types: list[str] = []
    filters: dict[str, Any] = {}


@router.post("/media/download")
async def media_download(request: Request, body: MediaDownloadIn) -> dict[str, Any]:
    from tgarchiver.media.downloader import enqueue

    svc = S(request)
    n = await enqueue(svc, body.chat_ids or None, body.media_types, body.filters)
    return {"queued": n, "job_id": await _ensure_media_job(svc)}


@router.post("/media/{media_id}/{action}")
async def media_action(request: Request, media_id: int, action: str) -> dict[str, Any]:
    svc = S(request)
    db = svc.db
    if action == "download-now":
        await db.execute("UPDATE media SET status='pending', priority=100, attempts=0, error=NULL, updated_at=? "
                         "WHERE id=? AND status!='done'", (now_iso(), media_id))
        return {"job_id": await _ensure_media_job(svc)}
    if action == "pause":
        await db.execute("UPDATE media SET status='paused' WHERE id=? AND status IN ('pending','downloading')",
                         (media_id,))
    elif action == "resume":
        await db.execute("UPDATE media SET status='pending' WHERE id=? AND status='paused'", (media_id,))
        await _ensure_media_job(svc)
    elif action == "cancel":
        await db.execute("UPDATE media SET status='skipped' WHERE id=? AND status!='done'", (media_id,))
    elif action == "retry":
        await db.execute("UPDATE media SET status='pending', attempts=0, error=NULL WHERE id=? AND status='failed'",
                         (media_id,))
        await _ensure_media_job(svc)
    else:
        raise HTTPException(404)
    svc.bus.emit("media.changed")
    return {"ok": True}


@router.post("/media-all/{action}")
async def media_all(request: Request, action: str, chat_id: int | None = Body(None, embed=True)) -> dict[str, Any]:
    svc = S(request)
    scope, args = ("AND chat_id=?", [chat_id]) if chat_id else ("", [])
    if action == "pause":
        await svc.db.execute(f"UPDATE media SET status='paused' WHERE status='pending' {scope}", args)
    elif action == "resume":
        await svc.db.execute(f"UPDATE media SET status='pending' WHERE status='paused' {scope}", args)
        await _ensure_media_job(svc)
    elif action == "cancel":
        await svc.db.execute(f"UPDATE media SET status='skipped' WHERE status IN ('pending','paused') {scope}", args)
    elif action == "retry":
        await svc.db.execute(f"UPDATE media SET status='pending', attempts=0 WHERE status='failed' {scope}", args)
        await _ensure_media_job(svc)
    else:
        raise HTTPException(404)
    svc.bus.emit("media.changed")
    return {"ok": True}


@router.get("/media/{media_id}/file")
async def media_file(request: Request, media_id: int) -> Any:
    row = await S(request).db.fetchone("SELECT path, status, mime FROM media WHERE id=?", (media_id,))
    if not row or row["status"] != "done" or not row["path"] or not Path(row["path"]).exists():
        raise HTTPException(404)
    return untrusted_file(row["path"], row["mime"] or None)  # Starlette handles Range


# ------------------------------------------------------------------ search
@router.get("/search")
async def do_search(request: Request, q: str = "", filters: str = "{}", limit: int = 50, offset: int = 0
                    ) -> dict[str, Any]:
    from tgarchiver.sync.search import search

    try:
        f = json.loads(filters or "{}")
    except ValueError as e:
        raise HTTPException(400, "bad filters") from e
    return await search(S(request).db, q, f, limit=min(limit, 200), offset=offset)


@router.post("/search/rebuild")
async def search_rebuild(request: Request) -> dict[str, Any]:
    from tgarchiver.sync.search import rebuild_fts

    await rebuild_fts(S(request).db)
    return {"ok": True}


# ------------------------------------------------------------------ presets
class PresetIn(BaseModel):
    kind: str = Field(pattern=r"^(search|export)$")
    name: str
    data: dict[str, Any] = {}


@router.get("/presets")
async def list_presets(request: Request, kind: str | None = None) -> dict[str, Any]:
    db = S(request).db
    rows = await (db.fetchall("SELECT * FROM presets WHERE kind=? ORDER BY name", (kind,)) if kind
                  else db.fetchall("SELECT * FROM presets ORDER BY kind, name"))
    for r in rows:
        r["data"] = loads(r["data"], {})
    return {"items": rows}


@router.post("/presets")
async def create_preset(request: Request, body: PresetIn) -> dict[str, Any]:
    pid = await S(request).db.execute("INSERT INTO presets(kind, name, data, updated_at) VALUES(?,?,?,?)",
                                      (body.kind, body.name, dumps(body.data), now_iso()))
    return {"id": pid}


@router.put("/presets/{pid}")
async def update_preset(request: Request, pid: int, body: PresetIn) -> dict[str, Any]:
    await S(request).db.execute("UPDATE presets SET kind=?, name=?, data=?, updated_at=? WHERE id=?",
                                (body.kind, body.name, dumps(body.data), now_iso(), pid))
    return {"ok": True}


@router.delete("/presets/{pid}")
async def delete_preset(request: Request, pid: int) -> dict[str, Any]:
    await S(request).db.execute("DELETE FROM presets WHERE id=?", (pid,))
    return {"ok": True}


@router.post("/presets/import")
async def import_presets(request: Request, items: list[PresetIn] = Body(...)) -> dict[str, Any]:
    db = S(request).db
    for p in items:
        await db.execute("INSERT INTO presets(kind, name, data, updated_at) VALUES(?,?,?,?)",
                         (p.kind, p.name, dumps(p.data), now_iso()))
    return {"imported": len(items)}


# ------------------------------------------------------------------ folders
class FolderIn(BaseModel):
    name: str
    color: str | None = None
    emoji: str | None = None
    parent_id: int | None = None


@router.get("/folders")
async def list_folders(request: Request) -> dict[str, Any]:
    rows = await S(request).db.fetchall(
        "SELECT f.*, (SELECT count(*) FROM folder_chats fc WHERE fc.folder_id=f.id) AS chats FROM folders f "
        "ORDER BY f.source DESC, f.position, f.name")
    return {"items": rows}


@router.post("/folders")
async def create_folder(request: Request, body: FolderIn) -> dict[str, Any]:
    db = S(request).db
    if body.parent_id:
        parent = await db.fetchone("SELECT parent_id FROM folders WHERE id=?", (body.parent_id,))
        if parent and parent["parent_id"]:
            raise HTTPException(400, "max_depth")  # 2 levels max (docs/07)
    fid = await db.execute("INSERT INTO folders(name, color, emoji, parent_id, source) VALUES(?,?,?,?,'user')",
                           (body.name, body.color, body.emoji, body.parent_id))
    S(request).bus.emit("folders.changed")
    return {"id": fid}


@router.put("/folders/{fid}")
async def update_folder(request: Request, fid: int, body: FolderIn) -> dict[str, Any]:
    await S(request).db.execute("UPDATE folders SET name=?, color=?, emoji=?, parent_id=? WHERE id=?",
                                (body.name, body.color, body.emoji, body.parent_id, fid))
    S(request).bus.emit("folders.changed")
    return {"ok": True}


@router.delete("/folders/{fid}")
async def delete_folder(request: Request, fid: int) -> dict[str, Any]:
    db = S(request).db
    async with db.tx() as c:
        await c.execute("DELETE FROM folder_chats WHERE folder_id=? OR folder_id IN (SELECT id FROM folders WHERE "
                        "parent_id=?)", (fid, fid))
        await c.execute("DELETE FROM folders WHERE id=? OR parent_id=?", (fid, fid))
    S(request).bus.emit("folders.changed")
    return {"ok": True}


@router.post("/folders/{fid}/chats")
async def folder_chats(request: Request, fid: int, add: list[int] = Body([]), remove: list[int] = Body([])
                       ) -> dict[str, Any]:
    db = S(request).db
    async with db.tx() as c:
        await c.executemany("INSERT OR IGNORE INTO folder_chats(folder_id, chat_id) VALUES(?,?)",
                            [(fid, x) for x in add])
        await c.executemany("DELETE FROM folder_chats WHERE folder_id=? AND chat_id=?", [(fid, x) for x in remove])
    S(request).bus.emit("folders.changed")
    return {"ok": True}


@router.post("/folders/import-telegram")
async def folders_import(request: Request) -> dict[str, Any]:
    from tgarchiver.sync.dialogs import import_tg_folders

    return {"imported": await import_tg_folders(S(request))}


# ------------------------------------------------------------------ mini apps
@router.get("/miniapps")
async def list_miniapps(request: Request) -> dict[str, Any]:
    rows = await S(request).db.fetchall(
        "SELECT a.*, c.title AS chat_title, (SELECT count(*) FROM mini_app_snapshots s WHERE s.mini_app_id=a.id) AS "
        "snapshots FROM mini_apps a LEFT JOIN chats c ON c.id=a.found_in_chat ORDER BY a.last_seen DESC")
    return {"items": rows}


@router.post("/miniapps/detect")
async def detect_miniapps(request: Request) -> dict[str, Any]:
    return {"job_id": await S(request).engine.submit("detect_miniapps", {}, title="detect_miniapps")}


class MiniAppOpenIn(BaseModel):
    mode: str = Field("manual", pattern=r"^(manual|auto)$")
    max_depth: int = 2
    max_clicks: int = 30
    duration_s: int = 0
    start_param: str | None = None
    video: bool = False


@router.post("/miniapps/{app_id}/open")
async def open_miniapp(request: Request, app_id: int, body: MiniAppOpenIn) -> dict[str, Any]:
    svc = S(request)
    params = {"mini_app_id": app_id, **body.model_dump()}
    job_id = await svc.engine.submit("miniapp_session", params, title="miniapp_session")
    params["snap_now_key"] = f"snap_now_{job_id}"
    await svc.db.execute("UPDATE jobs SET params=? WHERE id=?", (dumps(params), job_id))
    return {"job_id": job_id}


@router.post("/miniapps/sessions/{job_id}/snapshot")
async def miniapp_snap_now(request: Request, job_id: int) -> dict[str, Any]:
    await S(request).db.kv_set(f"snap_now_{job_id}", True)
    return {"ok": True}


@router.get("/miniapps/{app_id}/snapshots")
async def miniapp_snapshots(request: Request, app_id: int) -> dict[str, Any]:
    rows = await S(request).db.fetchall("SELECT * FROM mini_app_snapshots WHERE mini_app_id=? ORDER BY id DESC",
                                        (app_id,))
    return {"items": rows}


@router.get("/miniapps/snapshots/{sid}/states")
async def snapshot_states(request: Request, sid: int) -> dict[str, Any]:
    snap = await S(request).db.fetchone("SELECT * FROM mini_app_snapshots WHERE id=?", (sid,))
    if not snap:
        raise HTTPException(404)
    d = Path(snap["path"]) / "states"
    states = []
    for js in sorted(d.glob("*.json")):
        meta = json.loads(js.read_text(encoding="utf-8"))
        stem = js.stem
        states.append({"n": stem, **meta, "png": f"{stem}.png", "mhtml": f"{stem}.mhtml"
                       if (d / f"{stem}.mhtml").exists() else None, "html": f"{stem}.html"})
    return {"snapshot": snap, "states": states}


@router.get("/miniapps/snapshots/{sid}/file")
async def snapshot_file(request: Request, sid: int, name: str) -> Any:
    snap = await S(request).db.fetchone("SELECT * FROM mini_app_snapshots WHERE id=?", (sid,))
    if not snap:
        raise HTTPException(404)
    base = Path(snap["path"]).resolve()
    f = (base / name).resolve()
    if base not in f.parents or not f.is_file():
        raise HTTPException(404)
    return untrusted_file(f)


@router.post("/miniapps/snapshots/{sid}/replay")
async def snapshot_replay(request: Request, sid: int) -> dict[str, Any]:
    return {"job_id": await S(request).engine.submit("miniapp_replay", {"snapshot_id": sid}, title="miniapp_replay")}


# ------------------------------------------------------------------ transcribe
@router.post("/transcribe")
async def transcribe(request: Request, media_ids: list[int] = Body([]), chat_id: int | None = Body(None),
                     include_video: bool = Body(False)) -> dict[str, Any]:
    from tgarchiver.transcribe.whisper import available

    if not available():
        raise HTTPException(400, {"code": "whisper_not_installed"})
    if not media_ids and not chat_id:
        raise HTTPException(400, "nothing to transcribe")
    params = {"media_ids": media_ids, "chat_id": chat_id, "include_video": include_video}
    return {"job_id": await S(request).engine.submit("transcribe", params, title="transcribe")}


# ------------------------------------------------------------------ monitor + AI
@router.get("/monitor/unread")
async def monitor_unread(request: Request) -> dict[str, Any]:
    from tgarchiver.monitor.service import unread_overview

    svc = S(request)
    return {"items": await unread_overview(svc), "last_run": await svc.db.kv_get("monitor_last_run")}


@router.post("/monitor/run")
async def monitor_run(request: Request) -> dict[str, Any]:
    return {"job_id": await S(request).engine.submit("monitor", {}, title="monitor")}


class AIRunIn(BaseModel):
    task: str = "digest"
    scope: dict[str, Any] = {"unread": True}
    provider: str | None = None
    model: str | None = None
    question: str = ""
    refresh_unread: bool = False
    transcribe_voice: bool = False  # run Whisper on untranscribed voice/round messages in the scope first


@router.post("/ai/run")
async def ai_run(request: Request, body: AIRunIn) -> dict[str, Any]:
    svc = S(request)
    if not svc.settings.ai.enabled:
        raise HTTPException(400, {"code": "ai_disabled"})
    return {"job_id": await svc.engine.submit("ai", body.model_dump(), title=f"ai_{body.task}")}


@router.post("/ai/estimate")
async def ai_estimate(request: Request, body: AIRunIn) -> dict[str, Any]:
    from tgarchiver.ai.pipeline import estimate_job_tokens

    return await estimate_job_tokens(S(request), body.scope)


@router.get("/ai/providers")
async def ai_providers(request: Request) -> dict[str, Any]:
    from tgarchiver.ai.providers import DEFAULT_MODELS, KEY_NAMES

    svc = S(request)
    return {"items": [{"id": p, "default_model": m, "ready": p not in KEY_NAMES or svc.secrets.has(KEY_NAMES[p])}
                      for p, m in DEFAULT_MODELS.items()]}


@router.get("/ai/prompts")
async def ai_prompts(request: Request) -> dict[str, Any]:
    from tgarchiver.ai.pipeline import PROMPTS

    return {"defaults": PROMPTS, "overrides": S(request).settings.ai.prompts}


@router.get("/ai/reports")
async def ai_reports(request: Request, limit: int = 50) -> dict[str, Any]:
    rows = await S(request).db.fetchall(
        "SELECT id, kind, scope, provider, model, tokens_in, tokens_out, created_at FROM ai_reports ORDER BY id DESC "
        "LIMIT ?", (limit,))
    for r in rows:
        r["scope"] = loads(r["scope"], {})
    return {"items": rows}


@router.get("/ai/reports/{rid}")
async def ai_report(request: Request, rid: int) -> dict[str, Any]:
    r = await S(request).db.fetchone("SELECT * FROM ai_reports WHERE id=?", (rid,))
    if not r:
        raise HTTPException(404)
    r["scope"] = loads(r["scope"], {})
    r["result"] = loads(r["result"], {})
    return r


@router.delete("/ai/reports/{rid}")
async def ai_report_delete(request: Request, rid: int) -> dict[str, Any]:
    await S(request).db.execute("DELETE FROM ai_reports WHERE id=?", (rid,))
    return {"ok": True}
