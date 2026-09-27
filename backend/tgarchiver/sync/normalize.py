"""Pure conversion of Telethon objects into DB rows (docs/11). No I/O here."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from telethon import utils
from telethon.tl import types

MEDIA_TYPES = ("photo", "video", "round", "voice", "audio", "document", "sticker", "gif")
_LINK_RE = re.compile(r"https?://|t\.me/", re.I)
_MINIAPP_LINK_RE = re.compile(
    r"(?:https?://)?t\.me/([A-Za-z0-9_]{4,32}bot)(?:/([A-Za-z0-9_]{1,64}))?(?:\?(?:startapp|start_app)=?([^\s&]*))?",
    re.I,
)


def iso(d: datetime | None) -> str | None:
    return d.isoformat(timespec="seconds") if d else None


def chat_type(entity: Any, me_id: int | None = None) -> str:
    if isinstance(entity, types.User):
        if me_id is not None and entity.id == me_id or entity.is_self:
            return "saved"
        return "bot" if entity.bot else "user"
    if isinstance(entity, (types.Chat, types.ChatForbidden)):
        return "group"
    if isinstance(entity, (types.Channel, types.ChannelForbidden)):
        if getattr(entity, "megagroup", False):
            return "forum" if getattr(entity, "forum", False) else "supergroup"
        return "channel"
    return "unknown"


def display_name(entity: Any) -> str:
    if isinstance(entity, types.User):
        name = " ".join(p for p in (entity.first_name, entity.last_name) if p)
        return name or (entity.username or "") or (f"+{entity.phone}" if entity.phone else str(entity.id))
    return getattr(entity, "title", None) or str(getattr(entity, "id", ""))


def chat_row(dialog: Any, me_id: int | None, account_id: int = 1) -> dict[str, Any]:
    e = dialog.entity
    ctype = chat_type(e, me_id)
    d = dialog.dialog
    msg = dialog.message
    return {
        "id": utils.get_peer_id(e),
        "account_id": account_id,
        "access_hash": getattr(e, "access_hash", None),
        "type": ctype,
        "title": "Saved Messages" if ctype == "saved" else display_name(e),
        "username": getattr(e, "username", None),
        "is_forum": int(ctype == "forum"),
        "is_archived": int(bool(getattr(dialog, "archived", False) or getattr(d, "folder_id", None) == 1)),
        "unread_count": int(getattr(d, "unread_count", 0) or 0),
        "unread_mentions": int(getattr(d, "unread_mentions_count", 0) or 0),
        "read_inbox_max_id": int(getattr(d, "read_inbox_max_id", 0) or 0),
        "last_message_id": int(msg.id) if msg else 0,
        "last_message_at": iso(msg.date) if msg else None,
        "noforwards": int(bool(getattr(e, "noforwards", False))),
        "phone": getattr(e, "phone", None) if isinstance(e, types.User) else None,
        "is_contact": int(bool(getattr(e, "contact", False))),
    }


def classify_media(msg: Any) -> tuple[str | None, dict[str, Any] | None]:
    """Return (media_type, downloadable-info). Info is None for non-file media (geo, poll...)."""
    media = getattr(msg, "media", None)
    if media is None:
        return None, None
    if isinstance(media, types.MessageMediaPhoto):
        photo = media.photo
        if not isinstance(photo, types.Photo):
            return "photo", None
        size = 0
        for s in photo.sizes:
            if isinstance(s, types.PhotoSize):
                size = max(size, s.size)
            elif isinstance(s, types.PhotoSizeProgressive):
                size = max(size, max(s.sizes or [0]))
        return "photo", {"tg_file_id": photo.id, "mime": "image/jpeg", "size": size, "file_name": f"{msg.id}.jpg"}
    if isinstance(media, types.MessageMediaDocument):
        doc = media.document
        if not isinstance(doc, types.Document):
            return "document", None
        kind = "document"
        name = None
        duration = None
        for a in doc.attributes:
            if isinstance(a, types.DocumentAttributeFilename):
                name = a.file_name
            elif isinstance(a, types.DocumentAttributeAudio):
                kind = "voice" if a.voice else "audio"
                duration = a.duration
            elif isinstance(a, types.DocumentAttributeVideo):
                if kind not in ("gif", "sticker"):
                    kind = "round" if a.round_message else "video"
                duration = a.duration
            elif isinstance(a, types.DocumentAttributeSticker):
                kind = "sticker"
            elif isinstance(a, types.DocumentAttributeAnimated):
                kind = "gif"
        if getattr(media, "round", False):
            kind = "round"
        if getattr(media, "voice", False):
            kind = "voice"
        if not name:
            ext = utils.get_extension(doc) or ""
            name = f"{msg.id}{ext}"
        return kind, {
            "tg_file_id": doc.id,
            "mime": doc.mime_type,
            "size": int(doc.size or 0),
            "file_name": name,
            "duration": duration,
        }
    mapping = {
        types.MessageMediaGeo: "geo",
        types.MessageMediaGeoLive: "geo",
        types.MessageMediaVenue: "venue",
        types.MessageMediaContact: "contact",
        types.MessageMediaPoll: "poll",
        types.MessageMediaWebPage: "webpage",
        types.MessageMediaDice: "dice",
        types.MessageMediaGame: "game",
        types.MessageMediaInvoice: "invoice",
        types.MessageMediaStory: "story",
    }
    for cls, name in mapping.items():
        if isinstance(media, cls):
            return name, None
    return "other", None


def _buttons(msg: Any) -> list[list[dict[str, Any]]] | None:
    markup = getattr(msg, "reply_markup", None)
    rows = getattr(markup, "rows", None)
    if not rows:
        return None
    out: list[list[dict[str, Any]]] = []
    for row in rows:
        r: list[dict[str, Any]] = []
        for b in row.buttons:
            kind = getattr(b, "type", b)  # layer 2xx: KeyboardButton/KeyboardInlineButton(text, type=...)
            name = type(kind).__name__.removeprefix("InlineButtonType").removeprefix("ButtonType")
            item: dict[str, Any] = {"text": getattr(b, "text", ""), "type": name}
            url = getattr(kind, "url", None)
            if url:
                item["url"] = url
            r.append(item)
        out.append(r)
    return out


def _reactions(msg: Any) -> list[dict[str, Any]] | None:
    rs = getattr(getattr(msg, "reactions", None), "results", None)
    if not rs:
        return None
    out = []
    for r in rs:
        emo = getattr(r.reaction, "emoticon", None) or ("custom" if hasattr(r.reaction, "document_id") else "?")
        out.append({"emoji": emo, "count": r.count})
    return out


def _fwd(msg: Any) -> dict[str, Any] | None:
    f = getattr(msg, "fwd_from", None)
    if not f:
        return None
    return {
        "from_id": utils.get_peer_id(f.from_id) if f.from_id else None,
        "from_name": f.from_name or getattr(f, "post_author", None),
        "date": iso(f.date),
        "channel_post": getattr(f, "channel_post", None),
    }


def _entities(msg: Any) -> list[dict[str, Any]]:
    out = []
    for e in getattr(msg, "entities", None) or []:
        item = {"t": type(e).__name__.removeprefix("MessageEntity"), "o": e.offset, "l": e.length}
        if getattr(e, "url", None):
            item["url"] = e.url
        if getattr(e, "language", None):
            item["lang"] = e.language
        out.append(item)
    return out


def _media_extra(msg: Any) -> dict[str, Any] | None:
    m = getattr(msg, "media", None)
    if isinstance(m, types.MessageMediaPoll):
        answers = []
        results = {bytes(r.option): r.voters for r in (m.results.results or [])} if m.results else {}
        for a in m.poll.answers:
            text = a.text.text if hasattr(a.text, "text") else str(a.text)
            answers.append({"text": text, "voters": results.get(bytes(a.option))})
        q = m.poll.question
        return {"poll": {"question": q.text if hasattr(q, "text") else str(q), "answers": answers}}
    if isinstance(m, (types.MessageMediaGeo, types.MessageMediaGeoLive, types.MessageMediaVenue)):
        g = m.geo
        extra: dict[str, Any] = {"geo": {"lat": getattr(g, "lat", None), "long": getattr(g, "long", None)}}
        if isinstance(m, types.MessageMediaVenue):
            extra["geo"]["title"] = m.title
            extra["geo"]["address"] = m.address
        return extra
    if isinstance(m, types.MessageMediaContact):
        return {"contact": {"name": f"{m.first_name} {m.last_name}".strip(), "phone": m.phone_number}}
    if isinstance(m, types.MessageMediaWebPage) and isinstance(m.webpage, types.WebPage):
        return {"webpage": {"url": m.webpage.url, "title": m.webpage.title, "site": m.webpage.site_name}}
    return None


def topic_of(msg: Any) -> int:
    r = getattr(msg, "reply_to", None)
    if isinstance(r, types.MessageReplyHeader) and r.forum_topic:
        return int(r.reply_to_top_id or r.reply_to_msg_id or 0)
    return 0


def message_row(chat_id: int, msg: Any) -> dict[str, Any]:
    is_service = isinstance(msg, types.MessageService)
    text = "" if is_service else (msg.message or "")
    mtype, info = (None, None) if is_service else classify_media(msg)
    raw: dict[str, Any] = {}
    ents = [] if is_service else _entities(msg)
    if ents:
        raw["entities"] = ents
    if not is_service:
        extra = _media_extra(msg)
        if extra:
            raw.update(extra)
        if getattr(msg, "post_author", None):
            raw["post_author"] = msg.post_author
        if getattr(msg, "views", None):
            raw["views"] = msg.views
    reply_to = getattr(msg, "reply_to", None)
    reply_id = getattr(reply_to, "reply_to_msg_id", None) if isinstance(reply_to, types.MessageReplyHeader) else None
    if is_service:
        raw["action"] = {k: v for k, v in msg.action.to_dict().items() if isinstance(v, (str, int, float, bool))}
    has_link = bool(_LINK_RE.search(text)) or any(e["t"] in ("Url", "TextUrl") for e in ents)
    topic = topic_of(msg)
    if topic and reply_id == topic:
        reply_id = None  # replying to the topic root is just "posted in topic"
    if msg.from_id:
        sender = utils.get_peer_id(msg.from_id)
    else:  # private chats / channel posts carry no from_id
        sender = None if msg.out else utils.get_peer_id(msg.peer_id)
    return {
        "chat_id": chat_id,
        "id": msg.id,
        "topic_id": topic,
        "sender_id": sender,
        "out": int(bool(msg.out)),
        "date": iso(msg.date),
        "edit_date": iso(getattr(msg, "edit_date", None)),
        "text": text,
        "reply_to": reply_id,
        "fwd_from": _fwd(msg) if not is_service else None,
        "media_type": mtype,
        "has_media": int(info is not None),
        "has_link": int(has_link),
        "reactions": None if is_service else _reactions(msg),
        "buttons": None if is_service else _buttons(msg),
        "service_action": type(msg.action).__name__.removeprefix("MessageAction") if is_service else None,
        "noforwards": int(bool(getattr(msg, "noforwards", False))),
        "grouped_id": getattr(msg, "grouped_id", None),
        "raw": raw or None,
        "_media": info,
        "_media_type": mtype,
    }


def user_row(entity: Any) -> dict[str, Any] | None:
    if isinstance(entity, types.User):
        return {
            "id": entity.id,
            "first_name": entity.first_name,
            "last_name": entity.last_name,
            "username": entity.username,
            "phone": entity.phone,
            "is_bot": int(bool(entity.bot)),
        }
    if isinstance(entity, (types.Channel, types.Chat)):
        return {
            "id": utils.get_peer_id(entity),
            "first_name": entity.title,
            "last_name": None,
            "username": getattr(entity, "username", None),
            "phone": None,
            "is_bot": 0,
        }
    return None


def find_mini_apps(msg: Any) -> list[dict[str, Any]]:
    """Mini App candidates from a message: web-view buttons and t.me/<bot>/<app> or ?startapp links."""
    found: list[dict[str, Any]] = []
    markup = getattr(msg, "reply_markup", None)
    for row in getattr(markup, "rows", None) or []:
        for b in row.buttons:
            kind = getattr(b, "type", None)
            if isinstance(kind, (types.InlineButtonTypeWebView, types.ButtonTypeSimpleWebView)):
                found.append({"kind": "button" if isinstance(kind, types.InlineButtonTypeWebView) else "simple",
                              "url": kind.url, "title": b.text, "short_name": ""})
    text = getattr(msg, "message", None) or ""
    urls = [text] + [e.url for e in (getattr(msg, "entities", None) or []) if getattr(e, "url", None)]
    for chunk in urls:
        for m in _MINIAPP_LINK_RE.finditer(chunk):
            bot, app, start = m.group(1), m.group(2), m.group(3)
            if app or start is not None and "startapp" in m.group(0).lower():
                found.append({"kind": "app" if app else "main", "bot_username": bot, "short_name": app or "",
                              "url": m.group(0), "title": app or bot})
    return found
