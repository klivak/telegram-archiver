"""Background services started with the app, and the GitHub release check (docs/18)."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from tgarchiver import __version__
from tgarchiver.services import Services

log = logging.getLogger(__name__)
RELEASES_URL = "https://api.github.com/repos/zorya-tech-studio/tgarchiver/releases/latest"


async def start_background(svc: Services) -> None:
    from tgarchiver.monitor.service import MonitorService
    from tgarchiver.transcribe.whisper import WhisperRunner

    svc.whisper = WhisperRunner()
    svc.background.append(svc.whisper)
    mon = MonitorService(svc)
    mon.start()
    svc.monitor = mon
    svc.background.append(mon)


def _semver(v: str) -> tuple[int, ...]:
    out = []
    for p in v.lstrip("v").split("-")[0].split("."):
        try:
            out.append(int(p))
        except ValueError:
            out.append(0)
    return tuple(out)


async def check_updates(svc: Services, force: bool = False) -> dict[str, Any]:
    """At most once a day; sends nothing but a plain GET (no identifiers)."""
    s = svc.settings
    if not s.check_updates and not force:
        return {"enabled": False, "current": __version__}
    cached = await svc.db.kv_get("update_info")
    if not force and s.last_update_check and cached:
        last = datetime.fromisoformat(s.last_update_check)
        if datetime.now(UTC) - last < timedelta(days=1):
            return cached
    info: dict[str, Any] = {"enabled": True, "current": __version__, "latest": None, "available": False}
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get(RELEASES_URL, headers={"Accept": "application/vnd.github+json"})
            if r.status_code == 200:
                d = r.json()
                info.update(latest=d.get("tag_name"), url=d.get("html_url"), notes=(d.get("body") or "")[:4000])
                info["available"] = _semver(d.get("tag_name") or "0") > _semver(__version__)
    except httpx.HTTPError:
        info["error"] = "network"
    s.last_update_check = datetime.now(UTC).isoformat(timespec="seconds")
    svc.save_settings()
    await svc.db.kv_set("update_info", info)
    return info
