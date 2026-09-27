"""Local full-text search over the archive (SQLite FTS5, docs/07)."""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, Any

from tgarchiver.core.db import loads

if TYPE_CHECKING:
    from tgarchiver.core.db import Database

_TOKEN = re.compile(r'"[^"]+"|[^\s"]+')


def fts_query(q: str) -> str:
    """Turn user input into a safe FTS5 expression: quoted phrases kept, words prefix-matched, AND-ed."""
    parts = []
    for tok in _TOKEN.findall(q or ""):
        if tok.startswith('"') and tok.endswith('"') and len(tok) > 2:
            parts.append('"' + tok[1:-1].replace('"', "") + '"')
        else:
            clean = re.sub(r"[^\w]", " ", tok, flags=re.UNICODE).strip()
            for w in clean.split():
                parts.append(f'"{w}"*')
    return " AND ".join(parts)


async def search(db: Database, q: str, filters: dict[str, Any] | None = None, *, limit: int = 50,
                 offset: int = 0) -> dict[str, Any]:
    f = filters or {}
    t0 = time.perf_counter()
    where: list[str] = []
    args: list[Any] = []
    expr = fts_query(q)
    if expr:
        where.append("messages_fts MATCH ?")
        args.append(expr)
    chat_ids = [int(c) for c in f.get("chat_ids") or []]
    if f.get("folder_id"):
        rows = await db.fetchall("SELECT chat_id FROM folder_chats WHERE folder_id=?", (int(f["folder_id"]),))
        chat_ids += [r["chat_id"] for r in rows]
        if not chat_ids:
            return {"items": [], "total": 0, "took_ms": 0}
    if chat_ids:
        where.append(f"m.chat_id IN ({','.join('?' * len(chat_ids))})")
        args.extend(chat_ids)
    if f.get("sender_id"):
        where.append("m.sender_id=?")
        args.append(int(f["sender_id"]))
    if f.get("date_from"):
        where.append("m.date>=?")
        args.append(f["date_from"])
    if f.get("date_to"):
        where.append("m.date<=?")
        args.append(f["date_to"] + "T23:59:59")
    if f.get("media_type"):
        types_ = f["media_type"] if isinstance(f["media_type"], list) else [f["media_type"]]
        where.append(f"m.media_type IN ({','.join('?' * len(types_))})")
        args.extend(types_)
    if f.get("has_link"):
        where.append("m.has_link=1")
    if f.get("only_mine"):
        where.append("m.out=1")
    if f.get("has_transcript"):
        where.append("EXISTS(SELECT 1 FROM media d JOIN transcripts t ON t.media_id=d.id "
                     "WHERE d.chat_id=m.chat_id AND d.message_id=m.id)")
    if not where:
        return {"items": [], "total": 0, "took_ms": 0}
    w = " AND ".join(where)
    if expr:
        base = f"FROM messages_fts JOIN messages m ON m.rowid=messages_fts.rowid WHERE {w}"
        snippet = "snippet(messages_fts, -1, '<mark>', '</mark>', '…', 16)"
    else:
        base = f"FROM messages m WHERE {w}"
        snippet = "substr(m.text, 1, 200)"
    order = "m.date DESC" if f.get("sort", "date") == "date" or not expr else "rank"
    sql = (f"SELECT m.chat_id, m.id, m.date, m.sender_id, m.out, m.media_type, m.topic_id, {snippet} AS snippet, "
           f"c.title AS chat_title, u.first_name, u.last_name, u.username "
           f"{base.replace('WHERE', 'LEFT JOIN chats c ON c.id=m.chat_id LEFT JOIN users u ON u.id=m.sender_id WHERE', 1)} "
           f"ORDER BY {order} LIMIT ? OFFSET ?")
    items = await db.fetchall(sql, [*args, limit, offset])
    total = await db.scalar(f"SELECT count(*) {base}", args) if offset == 0 else None
    for it in items:
        it["sender_name"] = " ".join(p for p in (it.pop("first_name"), it.pop("last_name")) if p) or it.pop("username", None)
        it.pop("username", None)
    return {"items": items, "total": total, "took_ms": round((time.perf_counter() - t0) * 1000, 1)}


async def rebuild_fts(db: Database) -> None:
    async with db.tx() as c:
        await c.execute("DELETE FROM messages_fts")
        await c.execute("INSERT INTO messages_fts(rowid, text, extra) SELECT rowid, coalesce(text,''), '' FROM messages")
        await c.execute(
            "UPDATE messages_fts SET extra = coalesce((SELECT group_concat(coalesce(d.file_name,'') || ' ' || "
            "coalesce(t.text,''), ' ') FROM messages m JOIN media d ON d.chat_id=m.chat_id AND d.message_id=m.id "
            "LEFT JOIN transcripts t ON t.media_id=d.id WHERE m.rowid=messages_fts.rowid), '')")
        await c.execute("INSERT INTO messages_fts(messages_fts) VALUES('optimize')")


def parse_json_cols(row: dict[str, Any]) -> dict[str, Any]:
    for k in ("fwd_from", "reactions", "buttons", "raw"):
        if k in row:
            row[k] = loads(row[k])
    return row
