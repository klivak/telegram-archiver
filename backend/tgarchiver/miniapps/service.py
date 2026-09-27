"""Mini Apps: detect in bots/messages, get a signed WebView URL from Telegram (docs/08)."""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit, urlunsplit

from telethon.tl import types
from telethon.tl.functions.messages import (
    GetAttachMenuBotsRequest,
    RequestAppWebViewRequest,
    RequestMainWebViewRequest,
    RequestSimpleWebViewRequest,
    RequestWebViewRequest,
)
from telethon.tl.functions.users import GetFullUserRequest

from tgarchiver.core.db import now_iso
from tgarchiver.jobs.engine import FloodWait, JobContext
from tgarchiver.sync.dialogs import input_peer

if TYPE_CHECKING:
    from tgarchiver.services import Services

log = logging.getLogger(__name__)

PLATFORM = "tdesktop"
THEME = {"bg_color": "#ffffff", "text_color": "#000000", "hint_color": "#999999", "link_color": "#2481cc",
         "button_color": "#2481cc", "button_text_color": "#ffffff", "secondary_bg_color": "#f1f1f1"}


def strip_init_data(url: str) -> str:
    """Remove tgWebAppData (a login token for this Mini App) from a URL before storing/logging."""
    if not url:
        return url
    parts = urlsplit(url)
    frag = re.sub(r"(^|&)tgWebAppData=[^&]*", "", parts.fragment).lstrip("&")
    query = re.sub(r"(^|&)tgWebAppData=[^&]*", "", parts.query).lstrip("&")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, frag))


async def _upsert(svc: Services, bot_id: int | None, bot_username: str | None, kind: str, short_name: str,
                  title: str | None, url: str, chat_id: int | None = None) -> None:
    await svc.db.execute(
        "INSERT INTO mini_apps(bot_id, bot_username, kind, short_name, title, url, found_in_chat, found_in_msg, "
        "first_seen, last_seen) VALUES(?,?,?,?,?,?,?,NULL,?,?) ON CONFLICT(bot_id, kind, short_name, url) DO UPDATE "
        "SET last_seen=excluded.last_seen, title=coalesce(excluded.title, mini_apps.title), "
        "bot_username=coalesce(excluded.bot_username, mini_apps.bot_username)",
        (bot_id, bot_username, kind, short_name, title, strip_init_data(url), chat_id, now_iso(), now_iso()))


async def detect_job(svc: Services, ctx: JobContext) -> dict[str, Any]:
    client = await svc.tg.authorized_client()
    bots = await svc.db.fetchall("SELECT * FROM chats WHERE type='bot'")
    found = 0
    for i, b in enumerate(bots):
        await ctx.check()
        await ctx.progress(done=i, total=len(bots), current=b["title"])
        try:
            full = await svc.tg.call(lambda b=b: client(GetFullUserRequest(input_peer(b))))  # type: ignore[misc]
        except FloodWait:
            raise
        except Exception as e:  # noqa: BLE001
            log.info("full user for bot failed: %s", type(e).__name__)
            continue
        user = next((u for u in full.users if u.id == full.full_user.id), None)
        info = full.full_user.bot_info
        mb = getattr(info, "menu_button", None)
        if isinstance(mb, types.BotMenuButton):
            await _upsert(svc, b["id"], b["username"], "menu", "", mb.text or b["title"], mb.url, b["id"])
            found += 1
        if user is not None and getattr(user, "bot_has_main_app", False):
            await _upsert(svc, b["id"], b["username"], "main", "", b["title"], "", b["id"])
            found += 1
    try:
        res = await svc.tg.call(lambda: client(GetAttachMenuBotsRequest(hash=0)))
        users = {u.id: u for u in getattr(res, "users", [])}
        for ab in getattr(res, "bots", []):
            u = users.get(ab.bot_id)
            await _upsert(svc, ab.bot_id, getattr(u, "username", None), "attach", ab.short_name, ab.short_name, "")
            found += 1
    except FloodWait:
        raise
    except Exception as e:  # noqa: BLE001
        log.info("attach menu bots failed: %s", type(e).__name__)
    svc.bus.emit("miniapps.changed")
    return {"found": found, "bots": len(bots)}


async def _bot_input(svc: Services, app: dict[str, Any]) -> Any:
    client = await svc.tg.authorized_client()
    if app.get("bot_id"):
        row = await svc.db.fetchone("SELECT * FROM chats WHERE id=?", (app["bot_id"],))
        if row and row["access_hash"]:
            return input_peer(row)
    if app.get("bot_username"):
        return await svc.tg.call(lambda: client.get_input_entity(app["bot_username"]))
    if app.get("bot_id"):
        return await svc.tg.call(lambda: client.get_input_entity(app["bot_id"]))
    raise ValueError("bot is unknown")


def _to_input_user(peer: Any) -> Any:
    if isinstance(peer, types.InputPeerUser):
        return types.InputUser(peer.user_id, peer.access_hash)
    return peer


async def signed_url(svc: Services, app: dict[str, Any], start_param: str | None = None) -> str:
    """Ask Telegram for a signed Mini App URL (contains tgWebAppData - never store or log it)."""
    client = await svc.tg.authorized_client()
    bot_peer = await _bot_input(svc, app)
    bot_user = _to_input_user(bot_peer)
    theme = types.DataJSON(data=json.dumps(THEME))
    kind = app["kind"]
    url = app.get("url") or None
    if kind == "app":
        req: Any = RequestAppWebViewRequest(
            peer=types.InputPeerSelf(), app=types.InputBotAppShortName(bot_id=bot_user, short_name=app["short_name"]),
            platform=PLATFORM, start_param=start_param, theme_params=theme, write_allowed=False)
    elif kind in ("main",):
        req = RequestMainWebViewRequest(peer=bot_peer, bot=bot_user, platform=PLATFORM, start_param=start_param,
                                        theme_params=theme)
    elif kind in ("simple", "attach"):
        req = RequestSimpleWebViewRequest(bot=bot_user, platform=PLATFORM, url=url, from_side_menu=kind == "attach"
                                          or None, start_param=start_param, theme_params=theme)
    else:  # menu / button
        peer = bot_peer
        if kind == "button" and app.get("found_in_chat"):
            chat = await svc.db.fetchone("SELECT * FROM chats WHERE id=?", (app["found_in_chat"],))
            if chat:
                peer = input_peer(chat)
        req = RequestWebViewRequest(peer=peer, bot=bot_user, platform=PLATFORM, url=url,
                                    from_bot_menu=kind == "menu" or None, theme_params=theme)
    res = await svc.tg.call(lambda: client(req))
    return res.url
