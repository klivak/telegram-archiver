"""AI analysis: normalize -> mask PII -> chunk -> map-reduce -> validated JSON -> ai_reports (docs/10)."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field, ValidationError

from tgarchiver.ai.providers import CONTEXT_TOKENS, KEY_NAMES, make_provider
from tgarchiver.core.db import dumps, now_iso
from tgarchiver.export.exporter import estimate_tokens
from tgarchiver.jobs.engine import JobContext

if TYPE_CHECKING:
    from tgarchiver.services import Services

TASKS = ("digest", "priorities", "action_items", "topics", "qa")

SYSTEM = ("You analyse the user's own Telegram messages to help them catch up. Be concise and factual, never invent "
          "facts. Answer in {lang}. Reply with a single JSON object only, no prose around it.")

PROMPTS: dict[str, str] = {
    "digest": 'Summarise these messages per chat and overall. JSON: {"overall": str, "chats": [{"chat": str, '
              '"summary": str}]}',
    "priorities": "Find what needs the user's reply or action: questions to them, requests, deadlines. JSON: "
                  '{"items": [{"chat": str, "what": str, "why": str, "deadline": str|null, '
                  '"urgency": "high"|"medium"|"low"}]}',
    "action_items": 'Extract concrete tasks for the user. JSON: {"items": [{"task": str, "chat": str, '
                    '"due": str|null, "from": str|null}]}',
    "topics": 'Group the discussion into topics/trends across chats. JSON: {"topics": [{"title": str, '
              '"summary": str, "chats": [str]}]}',
    "qa": 'Answer the question using only these messages: "{question}". JSON: {"answer": str, '
          '"sources": [{"chat": str, "date": str}]}',
}
REDUCE = ("Merge these partial JSON results of the same task into one final JSON object with the same schema, "
          "removing duplicates:\n")

_PHONE = re.compile(r"(?<!\d)(?:\+?\d[\d\s\-()]{8,}\d)(?!\d)")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")


def mask_pii(text: str) -> str:
    text = _EMAIL.sub("[email]", text)
    text = _CARD.sub("[card]", text)
    return _PHONE.sub("[phone]", text)


class ChatSummary(BaseModel):
    chat: str = ""
    summary: str = ""


class Digest(BaseModel):
    overall: str = ""
    chats: list[ChatSummary] = Field(default_factory=list)


class Items(BaseModel):
    items: list[dict[str, Any]] = Field(default_factory=list)


class Topics(BaseModel):
    topics: list[dict[str, Any]] = Field(default_factory=list)


class Answer(BaseModel):
    answer: str = ""
    sources: list[dict[str, Any]] = Field(default_factory=list)


SCHEMAS: dict[str, type[BaseModel]] = {"digest": Digest, "priorities": Items, "action_items": Items,
                                       "topics": Topics, "qa": Answer}


def parse_json(text: str, task: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except ValueError:
        m = re.search(r"\{.*\}", text, re.S)
        if not m:
            raise ValueError("model did not return JSON") from None
        data = json.loads(m.group(0))
    try:
        return SCHEMAS[task].model_validate(data).model_dump()
    except ValidationError as e:
        raise ValueError(f"invalid JSON schema: {e.error_count()} errors") from e


def scope_where(svc: Services, scope: dict[str, Any]) -> tuple[list[str], list[Any]]:
    """SQL conditions (aliases: m=messages, c=chats) for an AI scope: unread, chats/folder, since/until/days."""
    where: list[str] = ["m.service_action IS NULL"]
    args: list[Any] = []
    if scope.get("unread"):
        where.append("m.out=0 AND m.id > c.read_inbox_max_id AND c.unread_count > 0")
    chat_ids = [int(c) for c in scope.get("chat_ids") or []]
    in_chats: list[str] = []
    if scope.get("folder_id"):
        in_chats.append("m.chat_id IN (SELECT chat_id FROM folder_chats WHERE folder_id=?)")
        args.append(int(scope["folder_id"]))
    if chat_ids:
        in_chats.append(f"m.chat_id IN ({','.join('?' * len(chat_ids))})")
        args.extend(chat_ids)
    if in_chats:
        where.append(f"({' OR '.join(in_chats)})")
    since = scope.get("since")
    if not since and scope.get("days"):
        since = (datetime.now() - timedelta(days=int(scope["days"]))).date().isoformat()
    if since:
        where.append("m.date >= ?")
        args.append(str(since))
    if scope.get("until"):
        # inclusive end day: compare against the next day's start
        until = (datetime.fromisoformat(str(scope["until"])[:10]) + timedelta(days=1)).date().isoformat()
        where.append("m.date < ?")
        args.append(until)
    ignore = svc.settings.monitor.ignore_chat_ids
    if ignore and scope.get("unread"):
        where.append(f"m.chat_id NOT IN ({','.join('?' * len(ignore))})")
        args.extend(ignore)
    return where, args


async def untranscribed_media(svc: Services, scope: dict[str, Any]) -> list[int]:
    """Voice/round messages in the scope that have no transcript yet."""
    where, args = scope_where(svc, scope)
    rows = await svc.db.fetchall(
        "SELECT d.id FROM messages m JOIN chats c ON c.id=m.chat_id "
        "JOIN media d ON d.chat_id=m.chat_id AND d.message_id=m.id LEFT JOIN transcripts t ON t.media_id=d.id "
        f"WHERE {' AND '.join(where)} AND d.type IN ('voice','round') AND t.media_id IS NULL ORDER BY d.id LIMIT 2000",
        args)
    return [r["id"] for r in rows]


async def collect_lines(svc: Services, scope: dict[str, Any]) -> list[tuple[str, str]]:
    """Return (chat_title, line) for the scope: unread messages or chats/folder in a period."""
    where, args = scope_where(svc, scope)
    sql = ("SELECT c.title, m.date, m.text, m.media_type, u.first_name, u.last_name, u.username, m.out, t.text AS tr "
           "FROM messages m JOIN chats c ON c.id=m.chat_id LEFT JOIN users u ON u.id=m.sender_id "
           "LEFT JOIN media d ON d.chat_id=m.chat_id AND d.message_id=m.id LEFT JOIN transcripts t ON t.media_id=d.id "
           f"WHERE {' AND '.join(where)} ORDER BY m.chat_id, m.id LIMIT 20000")
    out: list[tuple[str, str]] = []
    mask = svc.settings.ai.mask_pii
    for r in await svc.db.fetchall(sql, args):
        who = "me" if r["out"] else (" ".join(p for p in (r["first_name"], r["last_name"]) if p) or r["username"] or "?")
        body = r["text"] or ""
        if r["tr"]:
            body = f"{body} (voice: {r['tr']})".strip()
        if r["media_type"] and not body:
            body = f"[{r['media_type']}]"
        if mask:
            body = mask_pii(body)
        out.append((r["title"], f"[{(r['date'] or '')[5:16].replace('T', ' ')}] {who}: {body}"))
    return out


def chunk(lines: list[tuple[str, str]], budget: int) -> list[str]:
    chunks: list[str] = []
    cur: list[str] = []
    cur_tok, cur_chat = 0, None
    for chat, line in lines:
        piece = line if chat == cur_chat else f"\n## {chat}\n{line}"
        tok = estimate_tokens(piece)
        if cur and cur_tok + tok > budget:
            chunks.append("\n".join(cur))
            cur, cur_tok = [], 0
            piece = f"## {chat}\n{line}"
        cur.append(piece)
        cur_tok += tok
        cur_chat = chat
    if cur:
        chunks.append("\n".join(cur))
    return chunks


async def estimate_job_tokens(svc: Services, scope: dict[str, Any]) -> dict[str, Any]:
    lines = await collect_lines(svc, scope)
    voice = len(await untranscribed_media(svc, scope))
    tokens = sum(estimate_tokens(line) for _, line in lines)
    return {"messages": len(lines), "tokens": tokens, "untranscribed_voice": voice, "chats": len({c for c, _ in lines}),
            "today_used": await svc.db.kv_get(f"ai_tokens_{datetime.now().date().isoformat()}", 0),
            "daily_limit": svc.settings.ai.daily_token_limit}


async def ai_job(svc: Services, ctx: JobContext) -> dict[str, Any]:
    p = ctx.params
    task = p.get("task", "digest")
    if task not in TASKS:
        raise ValueError("unknown task")
    s = svc.settings.ai
    provider_name = p.get("provider") or s.provider
    key = svc.secrets.get(KEY_NAMES[provider_name]) if provider_name in KEY_NAMES else None
    provider = make_provider(provider_name, p.get("model") or s.model, key=key, ollama_url=s.ollama_url)
    scope = p.get("scope") or {"unread": True}
    if p.get("refresh_unread"):
        from tgarchiver.monitor.service import monitor_job

        await monitor_job(svc, ctx)
    if p.get("transcribe_voice") and svc.settings.whisper.enabled:
        from tgarchiver.transcribe.whisper import available, transcribe_job

        media_ids = await untranscribed_media(svc, scope)
        if media_ids and available():
            await ctx.progress(stage="transcribe", force=True)
            await transcribe_job(svc, ctx, {"media_ids": media_ids})
    lines = await collect_lines(svc, scope)
    if not lines:
        result: dict[str, Any] = {"empty": True}
        rid = await _save(svc, task, scope, provider, result, 0, 0)
        return {"report_id": rid, "empty": True}
    budget = CONTEXT_TOKENS.get(provider_name, 6000)
    parts = chunk(lines, budget)
    lang = "Ukrainian" if svc.settings.language == "uk" else "English"
    system = SYSTEM.format(lang=lang)
    prompt = (s.prompts.get(task) or PROMPTS[task]).replace("{question}", p.get("question", ""))
    today_key = f"ai_tokens_{datetime.now().date().isoformat()}"
    used = int(await svc.db.kv_get(today_key, 0))
    est = sum(estimate_tokens(x) for x in parts)
    if s.daily_token_limit and used + est > s.daily_token_limit:
        raise ValueError("ai_daily_limit")
    tin = tout = 0
    partials: list[dict[str, Any]] = []
    for i, part in enumerate(parts):
        await ctx.check()
        await ctx.progress(stage="map", done=i, total=len(parts), force=True)
        r = await provider.complete(system, f"{prompt}\n\nMessages:\n{part}")
        tin, tout = tin + r.tokens_in, tout + r.tokens_out
        partials.append(parse_json(r.text, task))
    if len(partials) == 1:
        final = partials[0]
    else:
        await ctx.progress(stage="reduce", force=True)
        r = await provider.complete(system, REDUCE + json.dumps(partials, ensure_ascii=False) + f"\n\nTask: {prompt}")
        tin, tout = tin + r.tokens_in, tout + r.tokens_out
        final = parse_json(r.text, task)
    await svc.db.kv_set(today_key, used + tin + tout)
    rid = await _save(svc, task, scope, provider, final, tin, tout)
    await _write_md(svc, rid, task, final)
    if p.get("scheduled"):
        svc.bus.emit("notification", {"code": "digest_ready", "report_id": rid})
    return {"report_id": rid, "tokens_in": tin, "tokens_out": tout}


async def _save(svc: Services, task: str, scope: dict[str, Any], provider: Any, result: dict[str, Any], tin: int,
                tout: int) -> int:
    rid = await svc.db.execute(
        "INSERT INTO ai_reports(kind, scope, provider, model, result, tokens_in, tokens_out, created_at) "
        "VALUES(?,?,?,?,?,?,?,?)", (task, dumps(scope), provider.name, provider.model, dumps(result), tin, tout,
                                    now_iso()))
    svc.bus.emit("ai.report", {"id": rid, "kind": task})
    return rid


def report_markdown(task: str, r: dict[str, Any]) -> str:
    out = [f"# {task} - {datetime.now().strftime('%Y-%m-%d %H:%M')}", ""]
    if task == "digest":
        out += [r.get("overall", ""), ""]
        for c in r.get("chats", []):
            out += [f"## {c.get('chat')}", c.get("summary", ""), ""]
    elif task in ("priorities", "action_items"):
        for it in r.get("items", []):
            out.append("- [ ] " + " · ".join(str(v) for v in it.values() if v))
    elif task == "topics":
        for t in r.get("topics", []):
            out += [f"## {t.get('title')}", t.get("summary", ""), ""]
    else:
        out.append(r.get("answer", ""))
    return "\n".join(out) + "\n"


async def _write_md(svc: Services, rid: int, task: str, result: dict[str, Any]) -> None:
    d = svc.account_dir / "ai" / datetime.now().date().isoformat()
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{task}_{rid}.md").write_text(report_markdown(task, result), encoding="utf-8")
    (d / f"{task}_{rid}.json").write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
