"""Playwright-based Mini App sessions: manual record, safe auto crawl, snapshots (MHTML/PNG/HTML/HAR/trace).

Requires the optional extra `[miniapps]` and `playwright install chromium`. Imported lazily.
"""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tgarchiver.core.db import now_iso
from tgarchiver.core.paths import safe_name
from tgarchiver.jobs.engine import JobContext
from tgarchiver.miniapps.service import signed_url, strip_init_data

if TYPE_CHECKING:
    from tgarchiver.services import Services

log = logging.getLogger(__name__)

# Auto crawl never touches anything that may pay, confirm, send or destroy (CLAUDE.md rule 6).
DANGEROUS = re.compile(
    r"pay|buy|purchase|checkout|order|confirm|approve|withdraw|transfer|send|delete|remove|donate|tip|subscribe|"
    r"invoice|stars?\b|⭐|💎|ton\b|wallet|connect|sign|claim|stake|swap|deposit|bet|"
    r"\bok\b|\byes\b|accept|agree|continue|upgrade|premium|join|vote|gift|mint|spin|allow|share|invite|"
    r"купи|оплат|підтвер|видал|надісл|вивест|переказ|підпис|гаманец|ставк|\bтак\b|згод|продовж|поділ|запрос|"
    r"купить|оплат|подтвер|удал|отправ|вывест|перевод|кошел|\bда\b|соглас|продолж|подел|пригла",
    re.I,
)
BLOCKED_EVENTS = {"web_app_open_invoice", "web_app_request_phone", "web_app_data_send", "web_app_switch_inline_query",
                  "web_app_request_wallet", "web_app_send_prepared_message", "web_app_open_tg_link"}

SHIM = r"""
(() => {
  const blocked = new Set(%BLOCKED%);
  window.__tgaEvents = [];
  const post = (eventType, eventData) => {
    const ev = {t: Date.now(), type: eventType, blocked: blocked.has(eventType)};
    window.__tgaEvents.push(ev);
    try { window.__tgaEvent && window.__tgaEvent(ev); } catch (e) {}
  };
  window.TelegramWebviewProxy = { postEvent: post };
  const wrap = (name) => { const o = history[name]; history[name] = function() { const r = o.apply(this, arguments);
    try { window.__tgaNav && window.__tgaNav(location.href); } catch (e) {} return r; }; };
  wrap('pushState'); wrap('replaceState');
})();
"""
TG_UA = ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
         "Chrome/126.0.0.0 Mobile Safari/537.36 Telegram-Android/11.0.0")


def available() -> bool:
    return importlib.util.find_spec("playwright") is not None


class Session:
    def __init__(self, out: Path) -> None:
        self.out = out
        self.states = 0
        self.hashes: set[str] = set()
        self.events: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()

    async def snapshot(self, page: Any, context: Any, label: str = "") -> bool:
        async with self._lock:
            try:
                await page.wait_for_load_state("networkidle", timeout=4000)
            except Exception:  # noqa: BLE001
                pass
            try:
                html = await page.evaluate("document.documentElement.outerHTML")
            except Exception:  # noqa: BLE001
                return False
            h = hashlib.sha1(re.sub(r"\s+", " ", html).encode("utf-8", "ignore")).hexdigest()
            if h in self.hashes:
                return False
            self.hashes.add(h)
            self.states += 1
            n = f"{self.states:03d}"
            d = self.out / "states"
            d.mkdir(parents=True, exist_ok=True)
            await page.screenshot(path=str(d / f"{n}.png"), full_page=True)
            (d / f"{n}.html").write_text(html, encoding="utf-8")
            try:
                cdp = await context.new_cdp_session(page)
                snap = await cdp.send("Page.captureSnapshot", {"format": "mhtml"})
                (d / f"{n}.mhtml").write_text(snap["data"], encoding="utf-8", newline="")
                await cdp.detach()
            except Exception as e:  # noqa: BLE001
                log.info("mhtml failed: %s", type(e).__name__)
            (d / f"{n}.json").write_text(json.dumps(
                {"url": strip_init_data(page.url), "label": label, "at": now_iso()}, ensure_ascii=False),
                encoding="utf-8")
            return True


async def _clickables(page: Any) -> list[dict[str, Any]]:
    return await page.evaluate(r"""() => {
      const els = [...document.querySelectorAll('a, button, [role=button], [onclick], input[type=button]')];
      return els.map((el, i) => {
        const r = el.getBoundingClientRect();
        const text = (el.innerText || el.value || el.getAttribute('aria-label') || el.title || '').trim().slice(0, 80);
        el.setAttribute('data-tga-idx', String(i));
        // Text of an enclosing clickable too: a harmless label inside a "Pay" button must not pass.
        const outer = el.parentElement && el.parentElement.closest('a, button, [role=button], [onclick]');
        const ctx = outer ? (outer.innerText || outer.getAttribute('aria-label') || '').trim().slice(0, 120) : '';
        return {idx: i, text, ctx, href: el.getAttribute('href') || '', visible: r.width > 2 && r.height > 2 &&
                getComputedStyle(el).visibility !== 'hidden'};
      }).filter(e => e.visible);
    }""")


def is_safe(el: dict[str, Any]) -> bool:
    blob = f"{el.get('text', '')} {el.get('ctx', '')} {el.get('href', '')}"
    if DANGEROUS.search(blob):
        return False
    href = el.get("href") or ""
    return not href.startswith(("tg:", "mailto:", "tel:", "https://t.me/$", "ton:"))


async def _click_safe(page: Any, text: str) -> bool:
    """Click exactly the inspected element (by index), re-checking it right before the click."""
    for el in await _clickables(page):
        if el["text"] != text or not is_safe(el):
            continue
        loc = page.locator(f'[data-tga-idx="{int(el["idx"])}"]')
        live = await loc.evaluate(
            "e => [(e.innerText || e.value || e.getAttribute('aria-label') || e.title || ''),"
            " (e.getAttribute('href') || '')].join(' ')")
        if DANGEROUS.search(live or ""):
            return False
        await loc.click(timeout=3000)
        return True
    return False


async def run_session(svc: Services, ctx: JobContext) -> dict[str, Any]:
    if not available():
        raise RuntimeError("playwright_not_installed")
    from playwright.async_api import async_playwright  # lazy, heavy

    p = ctx.params
    app = await svc.db.fetchone("SELECT * FROM mini_apps WHERE id=?", (int(p["mini_app_id"]),))
    if not app:
        raise ValueError("mini app not found")
    mode = p.get("mode", "manual")
    url = await signed_url(svc, app, p.get("start_param"))
    bot = safe_name(app["bot_username"] or str(app["bot_id"]), 40)
    name = safe_name(app["short_name"] or app["kind"], 40)
    out = svc.account_dir / "mini_apps" / bot / name / datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    profile = svc.account_dir / "mini_apps" / bot / f"profile_{name}"
    sess = Session(out)
    started = time.monotonic()
    duration = int(p.get("duration_s") or (900 if mode == "manual" else 300))
    await ctx.progress(stage="open", states=0, force=True)
    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            user_data_dir=str(profile), headless=mode == "auto" and not p.get("headful"),
            viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True, user_agent=TG_UA,
            record_har_path=str(out / "session.har"), record_har_content="attach",
            record_video_dir=str(out / "video") if p.get("video") else None,
        )
        try:
            await context.tracing.start(screenshots=True, snapshots=True, sources=False)
            await context.add_init_script(SHIM.replace("%BLOCKED%", json.dumps(sorted(BLOCKED_EVENTS))))
            closed = asyncio.Event()
            page = context.pages[0] if context.pages else await context.new_page()
            page.on("close", lambda *_: closed.set())
            pending: set[asyncio.Task[Any]] = set()

            def schedule_snap(label: str) -> None:
                t = asyncio.create_task(sess.snapshot(page, context, label))
                pending.add(t)
                t.add_done_callback(pending.discard)

            await context.expose_binding("__tgaEvent", lambda _src, ev: sess.events.append(ev))
            await context.expose_binding("__tgaNav", lambda _src, _u: schedule_snap("history"))
            page.on("framenavigated", lambda fr: schedule_snap("navigate") if fr == page.main_frame else None)
            await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            await sess.snapshot(page, context, "start")
            if mode == "auto":
                await _auto_crawl(page, context, sess, ctx, url, int(p.get("max_depth", 2)),
                                  int(p.get("max_clicks", 30)), started, duration)
            else:
                while not closed.is_set() and time.monotonic() - started < duration:
                    await ctx.check()
                    await ctx.progress(stage="recording", states=sess.states,
                                       elapsed=int(time.monotonic() - started))
                    try:
                        await asyncio.wait_for(closed.wait(), timeout=1.0)
                    except TimeoutError:
                        pass
                    if p.get("snap_now_key") and await svc.db.kv_get(p["snap_now_key"]):
                        await svc.db.kv_set(p["snap_now_key"], False)
                        await sess.snapshot(page, context, "manual")
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            await context.tracing.stop(path=str(out / "trace.zip"))
        finally:
            await context.close()
    meta = {"bot": app["bot_username"], "app": app["short_name"], "kind": app["kind"], "url": strip_init_data(url),
            "mode": mode, "states": sess.states, "events": sess.events[-200:], "created_at": now_iso(),
            "warning": "session.har and trace.zip may contain tokens and cookies - keep them private."}
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    snap_id = await svc.db.execute(
        "INSERT INTO mini_app_snapshots(mini_app_id, path, mode, states, created_at) VALUES(?,?,?,?,?)",
        (app["id"], str(out), mode, sess.states, now_iso()))
    svc.bus.emit("miniapps.changed")
    return {"snapshot_id": snap_id, "states": sess.states, "path": str(out)}


async def _auto_crawl(page: Any, context: Any, sess: Session, ctx: JobContext, start_url: str, max_depth: int,
                      max_clicks: int, started: float, duration: int) -> None:
    """Breadth-first walk over safe clickable elements; returns to the start URL and replays the path per click."""
    queue: list[list[str]] = [[]]
    clicks = 0
    seen_paths: set[tuple[str, ...]] = set()
    while queue and clicks < max_clicks and time.monotonic() - started < duration:
        path = queue.pop(0)
        if len(path) >= max_depth:
            continue
        if not await _replay(page, start_url, path):
            continue
        for el in await _clickables(page):
            if clicks >= max_clicks:
                break
            await ctx.check()
            if not el["text"] or not is_safe(el):
                continue
            new_path = [*path, el["text"]]
            if tuple(new_path) in seen_paths:
                continue
            seen_paths.add(tuple(new_path))
            if not await _replay(page, start_url, path):
                break
            try:
                if not await _click_safe(page, el["text"]):
                    continue
            except Exception:  # noqa: BLE001
                continue
            clicks += 1
            await asyncio.sleep(0.8)  # gentle pace
            if await sess.snapshot(page, context, " > ".join(new_path)):
                queue.append(new_path)
            await ctx.progress(stage="crawl", states=sess.states, clicks=clicks)


async def _replay(page: Any, start_url: str, path: list[str]) -> bool:
    try:
        await page.goto(start_url, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_load_state("networkidle", timeout=5000)
    except Exception:  # noqa: BLE001
        pass
    for text in path:
        try:
            if not await _click_safe(page, text):
                return False
            await asyncio.sleep(0.5)
        except Exception:  # noqa: BLE001
            return False
    return True


async def replay_har(svc: Services, ctx: JobContext) -> dict[str, Any]:
    """Open a saved session offline from its HAR (as far as the app allows)."""
    if not available():
        raise RuntimeError("playwright_not_installed")
    from playwright.async_api import async_playwright

    snap = await svc.db.fetchone("SELECT * FROM mini_app_snapshots WHERE id=?", (int(ctx.params["snapshot_id"]),))
    if not snap:
        raise ValueError("snapshot not found")
    out = Path(snap["path"])
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        context = await browser.new_context(viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True)
        await context.route_from_har(str(out / "session.har"), not_found="abort")
        page = await context.new_page()
        closed = asyncio.Event()
        page.on("close", lambda *_: closed.set())
        try:
            await page.goto(meta["url"], timeout=30_000)
        except Exception:  # noqa: BLE001
            pass
        while not closed.is_set():
            await ctx.check()
            try:
                await asyncio.wait_for(closed.wait(), timeout=1.0)
            except TimeoutError:
                pass
        await browser.close()
    return {"ok": True}
