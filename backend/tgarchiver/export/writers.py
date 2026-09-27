"""Streaming writers for MD / TXT / JSON / JSONL / HTML / CSV. Each part file is self-contained (docs/16)."""

from __future__ import annotations

import csv
import html
import io
import json
from typing import IO, Any

FORMATS = ("md", "txt", "json", "jsonl", "html", "csv")
PROTECTED_NOTE = "Protected content - personal archive only. Do not redistribute."

MEDIA_LABEL = {
    "photo": "Photo", "video": "Video", "round": "Video message", "voice": "Voice message", "audio": "Audio",
    "document": "File", "sticker": "Sticker", "gif": "GIF", "geo": "Location", "venue": "Venue",
    "contact": "Contact", "poll": "Poll", "webpage": "Link preview", "dice": "Dice", "game": "Game",
    "invoice": "Invoice", "story": "Story", "other": "Media",
}


# ---------- entity formatting (offsets are UTF-16 code units) ----------
def _utf16_slice(text: str) -> tuple[bytes, int]:
    b = text.encode("utf-16-le")
    return b, len(b) // 2


def _u16(b: bytes, start: int, end: int) -> str:
    return b[start * 2 : end * 2].decode("utf-16-le", errors="replace")


def format_entities(text: str, entities: list[dict[str, Any]] | None, mode: str) -> str:
    """Render Telegram entities to 'html' or 'md'. Unknown entities are rendered as plain text."""
    esc = html.escape if mode == "html" else (lambda s: s)
    if not text:
        return ""
    if not entities:
        return esc(text)
    b, n = _utf16_slice(text)
    opens: dict[int, list[str]] = {}
    closes: dict[int, list[str]] = {}
    for e in sorted(entities, key=lambda e: (e["o"], -e["l"])):
        o, ln, t = e["o"], e["l"], e["t"]
        if o < 0 or o + ln > n or ln <= 0:
            continue
        tags = _tags(t, e, mode, _u16(b, o, o + ln))
        if not tags:
            continue
        opens.setdefault(o, []).append(tags[0])
        closes.setdefault(o + ln, []).insert(0, tags[1])
    out: list[str] = []
    cut = sorted({0, n, *opens.keys(), *closes.keys()})
    for i, pos in enumerate(cut):
        out.extend(closes.get(pos, []))
        out.extend(opens.get(pos, []))
        if i + 1 < len(cut):
            out.append(esc(_u16(b, pos, cut[i + 1])))
    return "".join(out)


SAFE_SCHEMES = ("http://", "https://", "tg://", "mailto:")


def _safe_url(u: str) -> str:
    return u if u.lower().startswith(SAFE_SCHEMES) else "https://" + u


def _tags(t: str, e: dict[str, Any], mode: str, inner: str) -> tuple[str, str] | None:
    if t == "TextUrl" and not (e.get("url") or "").lower().startswith(SAFE_SCHEMES):
        return None  # javascript:, data: ... rendered as plain text
    if mode == "html":
        url = html.escape(e.get("url") or "", quote=True)
        return {
            "Bold": ("<b>", "</b>"), "Italic": ("<i>", "</i>"), "Underline": ("<u>", "</u>"),
            "Strike": ("<s>", "</s>"), "Code": ("<code>", "</code>"), "Pre": ("<pre>", "</pre>"),
            "Spoiler": ('<span class="spoiler">', "</span>"), "Blockquote": ("<blockquote>", "</blockquote>"),
            "TextUrl": (f'<a href="{url}" rel="noopener noreferrer">', "</a>"),
            "Url": (f'<a href="{html.escape(_safe_url(inner), quote=True)}" rel="noopener noreferrer">', "</a>"),
        }.get(t)
    return {
        "Bold": ("**", "**"), "Italic": ("_", "_"), "Strike": ("~~", "~~"), "Code": ("`", "`"),
        "Pre": ("\n```\n", "\n```\n"), "TextUrl": ("[", f"]({e.get('url', '')})"),
    }.get(t)


# ---------- message view model ----------
def sender_name(m: dict[str, Any]) -> str:
    if m.get("raw", {}).get("post_author"):
        return m["raw"]["post_author"]
    name = " ".join(p for p in (m.get("first_name"), m.get("last_name")) if p)
    return name or (m.get("username") or "") or (str(m["sender_id"]) if m.get("sender_id") else "")


def media_note(m: dict[str, Any]) -> str:
    t = m.get("media_type")
    if not t:
        return ""
    label = MEDIA_LABEL.get(t, t)
    raw = m.get("raw") or {}
    if t == "poll" and "poll" in raw:
        opts = "; ".join(f"{a['text']}" + (f" ({a['voters']})" if a.get("voters") is not None else "")
                         for a in raw["poll"]["answers"])
        return f"[{label}: {raw['poll']['question']} - {opts}]"
    if t in ("geo", "venue") and "geo" in raw:
        g = raw["geo"]
        return f"[{label}: {g.get('title') or ''} {g.get('lat')},{g.get('long')}]".replace("  ", " ")
    if t == "contact" and "contact" in raw:
        return f"[{label}: {raw['contact']['name']} {raw['contact']['phone']}]"
    if t == "webpage" and "webpage" in raw:
        return ""  # the URL is already in the text
    if m.get("file_name"):
        return f"[{label}: {m['file_name']}]"
    return f"[{label}]"


def service_text(m: dict[str, Any]) -> str:
    return f"[service: {m.get('service_action')}]"


class Writer:
    ext = "txt"

    def __init__(self, fh: IO[str], meta: dict[str, Any], opts: dict[str, Any]) -> None:
        self.fh = fh
        self.meta = meta
        self.opts = opts
        self.count = 0

    def begin(self) -> None: ...
    def write(self, m: dict[str, Any]) -> None: ...
    def end(self) -> None: ...

    def header_lines(self) -> list[str]:
        meta = self.meta
        lines = [f"{meta['chat_title']}"]
        if meta.get("part_label"):
            lines.append(f"Part {meta['part_no']}: {meta['part_label']}")
        if meta.get("noforwards"):
            lines.append(PROTECTED_NOTE)
        return lines


class TxtWriter(Writer):
    ext = "txt"

    def begin(self) -> None:
        self.fh.write("\n".join(self.header_lines()) + "\n\n")

    def write(self, m: dict[str, Any]) -> None:
        self.count += 1
        ts = (m["date"] or "")[:16].replace("T", " ")
        body = service_text(m) if m.get("service_action") else (m.get("text") or "")
        note = media_note(m)
        if note:
            body = f"{note} {body}".strip()
        self.fh.write(f"[{ts}] {sender_name(m)}: {body}\n")
        if m.get("transcript") and self.opts.get("include_transcripts", True):
            self.fh.write(f"    (transcript) {m['transcript']}\n")


class MdWriter(Writer):
    ext = "md"

    def begin(self) -> None:
        lines = self.header_lines()
        self.fh.write(f"# {lines[0]}\n\n" + "".join(f"> {line}\n" for line in lines[1:]) + "\n")
        self._day = ""

    def write(self, m: dict[str, Any]) -> None:
        self.count += 1
        day = (m["date"] or "")[:10]
        if day != self._day:
            self._day = day
            self.fh.write(f"\n## {day}\n\n")
        time = (m["date"] or "")[11:16]
        if m.get("service_action"):
            self.fh.write(f"*{time} {service_text(m)}*\n\n")
            return
        head = f"**{sender_name(m)}** · {time}"
        if m.get("fwd_from"):
            head += f" · forwarded from {m['fwd_from'].get('from_name') or m['fwd_from'].get('from_id') or '?'}"
        if m.get("reply_to"):
            head += f" · reply to #{m['reply_to']}"
        if m.get("edit_date"):
            head += " · edited"
        self.fh.write(f"{head} <a id=\"m{m['id']}\"></a>\n\n")
        text = format_entities(m.get("text") or "", (m.get("raw") or {}).get("entities"), "md")
        media = m.get("media_rel")
        if media and m.get("media_type") in ("photo", "sticker"):
            self.fh.write(f"![{m.get('file_name') or ''}]({media})\n\n")
        elif media:
            self.fh.write(f"[{media_note(m)}]({media})\n\n")
        elif media_note(m):
            self.fh.write(f"{media_note(m)}\n\n")
        if text:
            self.fh.write(text + "\n\n")
        if m.get("transcript") and self.opts.get("include_transcripts", True):
            self.fh.write(f"> 🎙 {m['transcript']}\n\n")
        if m.get("reactions"):
            self.fh.write(" ".join(f"{r['emoji']} {r['count']}" for r in m["reactions"]) + "\n\n")


def _json_msg(m: dict[str, Any]) -> dict[str, Any]:
    out = {
        "id": m["id"], "date": m["date"], "edit_date": m.get("edit_date"), "from_id": m.get("sender_id"),
        "from": sender_name(m), "out": bool(m.get("out")), "text": m.get("text") or "",
        "reply_to": m.get("reply_to"), "topic_id": m.get("topic_id") or None, "forwarded": m.get("fwd_from"),
        "media_type": m.get("media_type"), "file": m.get("media_rel"), "file_name": m.get("file_name"),
        "reactions": m.get("reactions"), "buttons": m.get("buttons"), "service": m.get("service_action"),
        "transcript": m.get("transcript"),
    }
    raw = m.get("raw") or {}
    for k in ("entities", "poll", "geo", "contact", "webpage", "views"):
        if k in raw:
            out[k] = raw[k]
    return {k: v for k, v in out.items() if v not in (None, [], "")}


class JsonlWriter(Writer):
    ext = "jsonl"

    def write(self, m: dict[str, Any]) -> None:
        self.count += 1
        self.fh.write(json.dumps(_json_msg(m), ensure_ascii=False) + "\n")


class JsonWriter(Writer):
    ext = "json"

    def begin(self) -> None:
        head = {"chat": {"id": self.meta["chat_id"], "title": self.meta["chat_title"], "type": self.meta["chat_type"]},
                "part": self.meta.get("part_no"), "label": self.meta.get("part_label")}
        if self.meta.get("noforwards"):
            head["notice"] = PROTECTED_NOTE
        s = json.dumps(head, ensure_ascii=False)
        self.fh.write(s[:-1] + ', "messages": [\n')

    def write(self, m: dict[str, Any]) -> None:
        if self.count:
            self.fh.write(",\n")
        self.count += 1
        self.fh.write(json.dumps(_json_msg(m), ensure_ascii=False))

    def end(self) -> None:
        self.fh.write("\n]}\n")


class CsvWriter(Writer):
    ext = "csv"
    COLS = ["id", "date", "from_id", "from", "out", "text", "reply_to", "media_type", "file", "transcript"]

    def begin(self) -> None:
        self.fh.write("﻿")  # Excel-friendly UTF-8
        self._w = csv.writer(self.fh)
        self._w.writerow(self.COLS)

    def write(self, m: dict[str, Any]) -> None:
        self.count += 1
        j = _json_msg(m)
        self._w.writerow([j.get("id"), j.get("date"), j.get("from_id"), j.get("from"), int(bool(m.get("out"))),
                          j.get("text", ""), j.get("reply_to") or "", j.get("media_type") or "", j.get("file") or "",
                          j.get("transcript") or ""])


HTML_CSS = """
:root{--bg:#e7ebf0;--card:#fff;--out:#e1ffc7;--fg:#111;--muted:#6b7a89;--link:#168acd}
@media(prefers-color-scheme:dark){:root{--bg:#0e1621;--card:#182533;--out:#2b5278;--fg:#e9eef3;--muted:#8a9aa9;--link:#6ab3f3}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.4 system-ui,-apple-system,Segoe UI,sans-serif}
header{position:sticky;top:0;background:var(--card);padding:10px 16px;box-shadow:0 1px 3px #0003;z-index:2}
header h1{font-size:17px;margin:0}header p{margin:2px 0 0;color:var(--muted);font-size:13px}
.notice{background:#fff3cd;color:#664d03;padding:6px 16px;font-size:13px}
main{max-width:760px;margin:0 auto;padding:12px}
.day{text-align:center;margin:14px 0 6px}.day span{background:#0003;color:#fff;border-radius:12px;padding:2px 10px;font-size:13px}
.msg{background:var(--card);border-radius:12px;padding:6px 10px;margin:4px 0;max-width:85%;width:fit-content;word-wrap:break-word}
.msg.out{background:var(--out);margin-left:auto}.svc{text-align:center;color:var(--muted);font-size:13px;margin:6px 0}
.from{font-weight:600;color:var(--link);font-size:14px}.meta{color:var(--muted);font-size:12px;text-align:right}
.fwd,.reply{color:var(--muted);font-size:13px;border-left:2px solid var(--link);padding-left:6px;margin:2px 0}
.msg img,.msg video{max-width:100%;max-height:420px;border-radius:8px;display:block;margin:4px 0}
.tr{font-size:13px;color:var(--muted);border-top:1px dashed #8884;margin-top:4px;padding-top:4px}
.react{font-size:13px;margin-top:2px}a{color:var(--link)}pre,code{background:#8882;border-radius:4px;padding:0 3px}
pre{padding:6px;overflow:auto}.spoiler{background:var(--muted);color:transparent}.spoiler:hover{color:inherit;background:none}
blockquote{margin:4px 0;padding-left:8px;border-left:3px solid var(--link)}
nav.parts{display:flex;justify-content:space-between;max-width:760px;margin:10px auto;padding:0 12px}
"""


class HtmlWriter(Writer):
    ext = "html"

    def begin(self) -> None:
        meta = self.meta
        t = html.escape(meta["chat_title"])
        label = html.escape(meta.get("part_label") or "")
        self.fh.write(
            f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" '
            f'content="width=device-width,initial-scale=1"><title>{t} {label}</title><style>{HTML_CSS}</style>'
            f'</head><body><header><h1>{t}</h1><p>{label}</p></header>'
        )
        if meta.get("noforwards"):
            self.fh.write(f'<div class="notice">🔒 {PROTECTED_NOTE}</div>')
        self.fh.write(self._nav() + "<main>")
        self._day = ""

    def _nav(self) -> str:
        prev, nxt = self.meta.get("prev_href"), self.meta.get("next_href")
        if not prev and not nxt and not self.meta.get("index_href"):
            return ""
        a = f'<a href="{html.escape(prev)}">← prev</a>' if prev else "<span></span>"
        i = f'<a href="{html.escape(self.meta["index_href"])}">index</a>' if self.meta.get("index_href") else ""
        b = f'<a href="{html.escape(nxt)}">next →</a>' if nxt else "<span></span>"
        return f'<nav class="parts">{a}{i}{b}</nav>'

    def write(self, m: dict[str, Any]) -> None:
        self.count += 1
        w = self.fh.write
        day = (m["date"] or "")[:10]
        if day != self._day:
            self._day = day
            w(f'<div class="day"><span>{day}</span></div>')
        if m.get("service_action"):
            w(f'<div class="svc" id="m{m["id"]}">{html.escape(service_text(m))}</div>')
            return
        cls = "msg out" if m.get("out") else "msg"
        w(f'<div class="{cls}" id="m{m["id"]}">')
        if not m.get("out"):
            w(f'<div class="from">{html.escape(sender_name(m))}</div>')
        if m.get("fwd_from"):
            f = m["fwd_from"]
            w(f'<div class="fwd">Forwarded from {html.escape(str(f.get("from_name") or f.get("from_id") or "?"))}</div>')
        if m.get("reply_to"):
            w(f'<div class="reply"><a href="#m{m["reply_to"]}">↩ #{m["reply_to"]}</a></div>')
        rel = m.get("media_rel")
        mt = m.get("media_type")
        if rel:
            src = html.escape(rel, quote=True)
            if mt in ("photo", "sticker") and not (m.get("file_name") or "").endswith((".tgs", ".webm")):
                w(f'<a href="{src}"><img loading="lazy" src="{src}" alt=""></a>')
            elif mt in ("video", "round", "gif") or (mt == "sticker"):
                w(f'<video controls preload="none" src="{src}"{" loop muted autoplay" if mt == "gif" else ""}></video>')
            elif mt in ("voice", "audio"):
                w(f'<audio controls preload="none" src="{src}"></audio>')
            else:
                w(f'<div>📎 <a href="{src}">{html.escape(m.get("file_name") or "file")}</a></div>')
        elif media_note(m):
            w(f"<div><i>{html.escape(media_note(m))}</i></div>")
        text = format_entities(m.get("text") or "", (m.get("raw") or {}).get("entities"), "html")
        if text:
            w(f'<div class="text">{text.replace(chr(10), "<br>")}</div>')
        if m.get("transcript") and self.opts.get("include_transcripts", True):
            w(f'<div class="tr">🎙 {html.escape(m["transcript"])}</div>')
        if m.get("reactions"):
            w('<div class="react">' + " ".join(f"{html.escape(str(r['emoji']))} {r['count']}" for r in m["reactions"])
              + "</div>")
        edited = " · edited" if m.get("edit_date") else ""
        w(f'<div class="meta">{(m["date"] or "")[11:16]}{edited}</div></div>')

    def end(self) -> None:
        self.fh.write("</main>" + self._nav() + "</body></html>\n")


WRITERS: dict[str, type[Writer]] = {
    "md": MdWriter, "txt": TxtWriter, "json": JsonWriter, "jsonl": JsonlWriter, "html": HtmlWriter, "csv": CsvWriter,
}


def render_to_string(fmt: str, meta: dict[str, Any], messages: list[dict[str, Any]],
                     opts: dict[str, Any] | None = None) -> str:
    """Test helper."""
    buf = io.StringIO()
    w = WRITERS[fmt](buf, meta, opts or {})
    w.begin()
    for m in messages:
        w.write(m)
    w.end()
    return buf.getvalue()
