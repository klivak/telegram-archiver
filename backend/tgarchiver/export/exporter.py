"""Chunked, streaming, incremental export of one chat into parts + index (docs/16).

Incremental re-export: parts are immutable except the last one, which is re-rendered from its first message.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tgarchiver.core.db import loads, now_iso
from tgarchiver.core.paths import long_path, safe_name
from tgarchiver.export.writers import WRITERS, Writer
from tgarchiver.media.downloader import chat_dir, media_path

if TYPE_CHECKING:
    from tgarchiver.jobs.engine import JobContext
    from tgarchiver.services import Services

SPLITS = ("single", "month", "day", "week", "year", "size", "topic", "llm")
BATCH = 500


def estimate_tokens(text: str) -> int:
    """Rough tokenizer-free estimate (~4 chars/token for Latin, ~2.5 for Cyrillic)."""
    if not text:
        return 1
    non_ascii = sum(1 for ch in text if ord(ch) > 127)
    return max(1, int((len(text) - non_ascii) / 4 + non_ascii / 2.5))


class Exporter:
    def __init__(self, svc: Services, chat: dict[str, Any], fmt: str, split: dict[str, Any],
                 opts: dict[str, Any] | None = None) -> None:
        self.svc = svc
        self.chat = chat
        self.fmt = fmt
        self.split = {"mode": "month", **(split or {})}
        self.opts = opts or {}
        self.base = chat_dir(svc, chat)
        self.out_dir = self.base / "export" / fmt
        self.topics: dict[int, str] = {}

    # ---------- part keys ----------
    def _period_key(self, date: str) -> str:
        mode = self.split["mode"]
        if mode == "year":
            return date[:4]
        if mode == "day":
            return date[:10]
        if mode == "week":
            from datetime import date as d

            y, w, _ = d.fromisoformat(date[:10]).isocalendar()
            return f"{y}-W{w:02d}"
        return date[:7]

    def _rel_path(self, key: str, part_no: int) -> str:
        ext = WRITERS[self.fmt].ext
        mode = self.split["mode"]
        if mode == "single":
            return f"messages.{ext}"
        if mode in ("size", "llm"):
            return f"part-{part_no:04d}.{ext}"
        if mode == "topic":
            topic_id, period = key.split("|", 1)
            tname = self.topics.get(int(topic_id)) or ("General" if topic_id == "0" else f"topic-{topic_id}")
            return f"{safe_name(tname, 60)}_{topic_id}/{period}.{ext}"
        return f"{key[:4]}/{key}.{ext}"

    # ---------- message stream ----------
    def _query(self, after_id: int) -> tuple[str, list[Any]]:
        where = ["m.chat_id=?", "m.id>?"]
        args: list[Any] = [self.chat["id"], after_id]
        if self.opts.get("date_from"):
            where.append("m.date>=?")
            args.append(self.opts["date_from"])
        if self.opts.get("date_to"):
            where.append("m.date<=?")
            args.append(self.opts["date_to"] + "T23:59:59")
        if self.opts.get("topic_id") is not None:
            where.append("m.topic_id=?")
            args.append(int(self.opts["topic_id"]))
        order = "m.topic_id, m.id" if self.split["mode"] == "topic" else "m.id"
        sql = (
            "SELECT m.*, u.first_name, u.last_name, u.username, d.id AS media_id, d.file_name, d.path AS media_path, "
            "d.status AS media_status, d.size AS media_size, d.type AS media_kind, d.date AS media_date, "
            "t.text AS transcript FROM messages m "
            "LEFT JOIN users u ON u.id=m.sender_id LEFT JOIN media d ON d.chat_id=m.chat_id AND d.message_id=m.id "
            f"LEFT JOIN transcripts t ON t.media_id=d.id WHERE {' AND '.join(where)} ORDER BY {order}"
        )
        return sql, args

    def _prepare(self, m: dict[str, Any], part_file: Path) -> dict[str, Any]:
        for k in ("fwd_from", "reactions", "buttons", "raw"):
            m[k] = loads(m.get(k))
        m["raw"] = m["raw"] or {}
        if m.get("media_id") and m.get("media_status") in ("pending", "downloading", "done"):
            # Paths are deterministic, so queued media is linked before it finishes downloading.
            target = m.get("media_path") or str(media_path(self.svc, self.chat, {
                "type": m["media_kind"], "file_name": m.get("file_name"), "message_id": m["id"],
                "date": m.get("media_date")}))
            try:
                m["media_rel"] = Path(os.path.relpath(target, part_file.parent)).as_posix()
            except ValueError:  # different drive on Windows
                m["media_rel"] = Path(target).as_uri()
        return m

    # ---------- run ----------
    def _load_index(self) -> dict[str, Any]:
        p = self.out_dir / "index.json"
        if p.exists():
            try:
                idx = json.loads(p.read_text(encoding="utf-8"))
                if idx.get("split") == self.split and idx.get("opts_key") == self._opts_key():
                    return idx
            except ValueError:
                pass
        return {"parts": []}

    def _opts_key(self) -> str:
        keys = ("date_from", "date_to", "topic_id", "include_transcripts")
        return json.dumps({k: self.opts.get(k) for k in keys}, sort_keys=True)

    async def run(self, ctx: JobContext | None = None) -> dict[str, Any]:
        self.out_dir.mkdir(parents=True, exist_ok=True)
        if self.split["mode"] == "topic":
            rows = await self.svc.db.fetchall("SELECT id, title FROM topics WHERE chat_id=?", (self.chat["id"],))
            self.topics = {r["id"]: r["title"] for r in rows}
        index = self._load_index()
        if not index["parts"]:
            # Split settings changed or first run: clean previous parts of this format.
            for f in self.out_dir.rglob(f"*.{WRITERS[self.fmt].ext}"):
                f.unlink()
        parts: list[dict[str, Any]] = index["parts"]
        after_id = 0
        if parts and self.split["mode"] != "topic":
            last = parts.pop()
            (self.out_dir / last["file"]).unlink(missing_ok=True)
            after_id = last["first_id"] - 1
        elif parts:  # topic ordering is not by id; re-render all
            for p in parts:
                (self.out_dir / p["file"]).unlink(missing_ok=True)
            parts = []
        cur: dict[str, Any] | None = None
        writer: Writer | None = None
        fh = None
        size_limit = int(float(self.split.get("size_mb") or 5) * 1024 * 1024)
        msg_limit = int(self.split.get("messages") or 0)
        tok_limit = int(self.split.get("tokens") or 100_000)
        overlap = int(self.split.get("overlap") or 20) if self.split["mode"] == "llm" else 0
        tail: list[dict[str, Any]] = []
        acc = 0
        total = 0

        def close() -> None:
            nonlocal fh, writer, cur
            if writer and fh and cur is not None:
                writer.end()
                fh.close()
                cur["messages"] = writer.count
                cur["bytes"] = (self.out_dir / cur["file"]).stat().st_size
                parts.append(cur)
            fh, writer, cur = None, None, None

        def open_part(key: str, m: dict[str, Any]) -> None:
            nonlocal fh, writer, cur, acc
            no = len(parts) + 1
            rel = self._rel_path(key, no)
            if any(p["file"] == rel for p in parts):
                # Same period seen again (imported history: old dates with newer ids) - never overwrite a part.
                stem, _dot, ext = rel.rpartition(".")
                rel = f"{stem}_p{no}.{ext}"
            path = self.out_dir / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            cur = {"no": no, "key": key, "file": rel, "first_id": m["id"], "last_id": m["id"],
                   "date_from": m["date"], "date_to": m["date"]}
            meta = {"chat_id": self.chat["id"], "chat_title": self.chat["title"], "chat_type": self.chat["type"],
                    "noforwards": bool(self.chat.get("noforwards")), "part_no": no,
                    "part_label": key if self.split["mode"] not in ("single",) else "",
                    "index_href": os.path.relpath(self.out_dir / "index.html", path.parent).replace("\\", "/")
                    if self.fmt == "html" else None}
            fh = open(long_path(path), "w", encoding="utf-8", newline="")
            writer = WRITERS[self.fmt](fh, meta, self.opts)
            writer.begin()
            acc = 0

        sql, args = self._query(after_id)
        try:
            async for batch in self.svc.db.iterate(sql, args, batch=BATCH):
                if ctx:
                    await ctx.check()
                for m in batch:
                    mode = self.split["mode"]
                    if mode == "single":
                        key = "all"
                    elif mode in ("size", "llm"):
                        key = cur["key"] if cur else f"part-{len(parts) + 1}"
                    elif mode == "topic":
                        key = f"{m['topic_id'] or 0}|{self._period_key(m['date'] or '')}"
                    else:
                        key = self._period_key(m["date"] or "0000-00")
                    weight = len((m.get("text") or "").encode("utf-8")) + 120
                    if mode == "llm":
                        weight = estimate_tokens(m.get("text") or "") + 12
                    need_new = cur is None or key != cur["key"]
                    too_many = bool(msg_limit and writer and writer.count >= msg_limit)
                    if cur is not None and mode == "size" and (acc + weight > size_limit or too_many):
                        need_new, key = True, f"part-{len(parts) + 2}"
                    if cur is not None and mode == "llm" and acc + weight > tok_limit:
                        need_new, key = True, f"part-{len(parts) + 2}"
                    if need_new:
                        close()
                        open_part(key, m)
                        if overlap and tail and writer:
                            for t in tail[-overlap:]:
                                writer.write(self._prepare(dict(t), self.out_dir / cur["file"]))  # type: ignore[index]
                    assert writer is not None and cur is not None
                    pm = self._prepare(m, self.out_dir / cur["file"])
                    writer.write(pm)
                    acc += weight
                    cur["last_id"] = m["id"]
                    cur["date_to"] = m["date"]
                    total += 1
                    if overlap:
                        tail.append(m)
                        tail = tail[-overlap:]
                if fh:
                    fh.flush()
                if ctx:
                    await ctx.progress(export_done=total)
        except BaseException:
            if fh is not None:  # paused/cancelled/failed mid-part: release the handle (Windows file locks)
                fh.close()
            raise
        close()
        await self._write_index(parts)
        await self.svc.db.execute(
            "INSERT INTO exports(chat_id, format, split, path, parts, messages, last_message_id, created_at) "
            "VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(chat_id, format, split) DO UPDATE SET path=excluded.path, "
            "parts=excluded.parts, messages=excluded.messages, last_message_id=excluded.last_message_id, "
            "created_at=excluded.created_at",
            (self.chat["id"], self.fmt, json.dumps(self.split, sort_keys=True), str(self.out_dir), len(parts),
             sum(p.get("messages", 0) for p in parts), parts[-1]["last_id"] if parts else 0, now_iso()))
        return {"parts": len(parts), "messages": sum(p.get("messages", 0) for p in parts), "dir": str(self.out_dir)}

    async def _write_index(self, parts: list[dict[str, Any]]) -> None:
        idx = {
            "chat": {"id": self.chat["id"], "title": self.chat["title"], "type": self.chat["type"],
                     "username": self.chat.get("username"), "phone": self.chat.get("phone")},
            "protected": bool(self.chat.get("noforwards")),
            "format": self.fmt, "split": self.split, "opts_key": self._opts_key(), "generated_at": now_iso(),
            "parts": parts,
        }
        (self.out_dir / "index.json").write_text(json.dumps(idx, ensure_ascii=False, indent=1), encoding="utf-8")
        lines = [f"# {self.chat['title']}", ""]
        if self.chat.get("username"):
            lines.append(f"@{self.chat['username']}")
        if self.chat.get("phone"):
            lines.append(f"+{self.chat['phone']}")
        if self.chat.get("noforwards"):
            lines.append("> 🔒 Protected content - personal archive only. Do not redistribute.")
        lines += ["", "| # | File | From | To | Messages |", "|---|---|---|---|---|"]
        for p in parts:
            lines.append(f"| {p['no']} | [{p['file']}]({p['file']}) | {(p['date_from'] or '')[:10]} | "
                         f"{(p['date_to'] or '')[:10]} | {p.get('messages', 0)} |")
        (self.out_dir / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        if self.fmt == "html":
            import html as h

            rows = "".join(
                f"<tr><td>{p['no']}</td><td><a href=\"{h.escape(p['file'])}\">{h.escape(p['file'])}</a></td>"
                f"<td>{(p['date_from'] or '')[:10]}</td><td>{(p['date_to'] or '')[:10]}</td>"
                f"<td>{p.get('messages', 0)}</td></tr>" for p in parts)
            (self.out_dir / "index.html").write_text(
                f"<!doctype html><meta charset=utf-8><title>{h.escape(self.chat['title'])}</title>"
                "<style>body{font:15px system-ui;max-width:760px;margin:20px auto}td,th{padding:4px 10px}</style>"
                f"<h1>{h.escape(self.chat['title'])}</h1>"
                + ("<p>🔒 Protected content - personal archive only.</p>" if self.chat.get("noforwards") else "")
                + f"<table><tr><th>#</th><th>File</th><th>From</th><th>To</th><th>Messages</th></tr>{rows}</table>",
                encoding="utf-8")


async def export_full_file(svc: Services, chat: dict[str, Any], fmt: str, opts: dict[str, Any]) -> str:
    """'Also build one full file' - streamed, independent of the part split."""
    ex = Exporter(svc, chat, fmt, {"mode": "single"}, opts)
    ex.out_dir = ex.base / "export" / f"{fmt}-full"
    await ex.run()
    return str(ex.out_dir)
