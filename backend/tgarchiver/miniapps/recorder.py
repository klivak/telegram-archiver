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


# job ids asked to "finish and save" (stop recording/crawling but keep everything recorded so far)
FINISH: set[int] = set()


class Session:
    def __init__(self, out: Path, job_id: int | None = None) -> None:
        self.out = out
        self.job_id = job_id
        self.states = 0
        self.hashes: set[str] = set()
        self.events: list[dict[str, Any]] = []
        self.pages: list[dict[str, str]] = []  # readable text of every saved state, for site.md / index.html
        self._lock = asyncio.Lock()

    @property
    def stop(self) -> bool:
        return self.job_id is not None and self.job_id in FINISH

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
            clean_url = strip_init_data(page.url)
            (d / f"{n}.json").write_text(json.dumps(
                {"url": clean_url, "label": label, "at": now_iso()}, ensure_ascii=False),
                encoding="utf-8")
            try:
                title, text = await page.evaluate(
                    "() => [document.title || '', document.body ? document.body.innerText : '']")
            except Exception:  # noqa: BLE001
                title, text = "", ""
            text = re.sub(r"\n{3,}", "\n\n", (text or "").strip())
            (d / f"{n}.md").write_text(f"# {title or label or n}\n\n> {clean_url}\n\n{text}\n", encoding="utf-8")
            self.pages.append({"n": n, "title": title or label or n, "label": label, "url": clean_url, "text": text})
            return True

    def write_site(self) -> None:
        """One readable file with the text of every page (site.md) and an offline index.html."""
        import html as _html

        if not self.pages:
            return
        seen: set[str] = set()
        md = ["# Mini App - saved pages", ""]
        cards = []
        for pg in self.pages:
            key = hashlib.sha1(pg["text"].encode("utf-8", "ignore")).hexdigest()
            dup = key in seen
            seen.add(key)
            if not dup and pg["text"]:
                md += [f"## {pg['n']} · {pg['title']}", "", f"> {pg['url']}" + (f" · {pg['label']}" if pg["label"] else ""),
                       "", pg["text"], "", "---", ""]
            n = pg["n"]
            cards.append(
                f'<section id="s{n}"><h2>{n} · {_html.escape(pg["title"])}</h2>'
                f'<p class="u">{_html.escape(pg["url"])}</p>'
                f'<p><a href="states/{n}.mhtml">MHTML</a> · <a href="states/{n}.html">HTML</a> · '
                f'<a href="states/{n}.md">text</a></p>'
                f'<a href="states/{n}.png"><img src="states/{n}.png" loading="lazy"></a>'
                f'<pre>{_html.escape(pg["text"][:20000]) if not dup else "(same text as an earlier page)"}</pre></section>')
        (self.out / "site.md").write_text("\n".join(md), encoding="utf-8")
        toc = "".join(f'<li><a href="#s{p["n"]}">{p["n"]} · {_html.escape(p["title"])}</a></li>' for p in self.pages)
        (self.out / "index.html").write_text(
            "<!doctype html><meta charset=utf-8><title>Mini App</title><style>"
            "body{font:14px system-ui;max-width:980px;margin:24px auto;padding:0 16px;background:#0f1115;color:#e6e6e6}"
            "a{color:#2aabee}section{border-top:1px solid #333;padding:16px 0}img{max-width:360px;border-radius:8px}"
            "pre{white-space:pre-wrap;background:#171a21;padding:12px;border-radius:8px}.u{color:#888;font-size:12px}"
            f"</style><h1>Mini App - {len(self.pages)} pages</h1><ol>{toc}</ol>{''.join(cards)}",
            encoding="utf-8")


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


DOH_URL = "https://cloudflare-dns.com/dns-query"


async def doh_resolve(host: str) -> str | None:
    """IPv4 of ``host`` via DNS-over-HTTPS. Some ISPs answer Mini App hosts (e.g. *.pages.dev) with their own server,
    which breaks TLS with ERR_CERT_COMMON_NAME_INVALID; the real address avoids that."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=6.0) as c:
            r = await c.get(DOH_URL, params={"name": host, "type": "A"}, headers={"accept": "application/dns-json"})
            r.raise_for_status()
            for a in r.json().get("Answer", []):
                if a.get("type") == 1 and re.fullmatch(r"\d+\.\d+\.\d+\.\d+", str(a.get("data", ""))):
                    return str(a["data"])
    except Exception as e:  # noqa: BLE001
        log.info("DoH lookup failed: %s", type(e).__name__)
    return None


async def resolver_args(url: str) -> list[str]:
    """Chromium flag pinning the Mini App host to its DoH address (empty if the lookup fails)."""
    from urllib.parse import urlsplit

    host = urlsplit(url).hostname or ""
    if not host or re.fullmatch(r"[\d.]+", host):
        return []
    ip = await doh_resolve(host)
    return [f"--host-resolver-rules=MAP {host} {ip}"] if ip else []


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
    sess = Session(out, ctx.id)
    started = time.monotonic()
    duration = int(p.get("duration_s") or (900 if mode == "manual" else 1800))
    title = app["title"] or app["short_name"] or app["bot_username"] or ""
    base = {"app_title": title, "mode": mode, "duration": duration, "max_clicks": int(p.get("max_clicks", 30))}
    await ctx.progress(stage="open", states=0, **base, force=True)
    dns_args = await resolver_args(url)
    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(
            user_data_dir=str(profile), headless=mode == "auto" and not p.get("headful"), args=dns_args,
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
                while not closed.is_set() and not sess.stop and time.monotonic() - started < duration:
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
            try:
                await context.tracing.stop(path=str(out / "trace.zip"))
            except Exception as e:  # noqa: BLE001
                # closing the window can drop screencast frames the trace refers to; the snapshots are already saved
                log.warning("mini app trace not saved: %s", type(e).__name__)
        finally:
            try:
                await context.close()
            except Exception as e:  # noqa: BLE001
                log.info("browser context close: %s", type(e).__name__)
    FINISH.discard(ctx.id)
    await ctx.progress(stage="saving", states=sess.states, force=True)
    sess.write_site()
    meta = {"bot": app["bot_username"], "app": app["short_name"], "kind": app["kind"], "url": strip_init_data(url),
            "mode": mode, "states": sess.states, "events": sess.events[-200:], "created_at": now_iso(),
            "warning": "session.har and trace.zip may contain tokens and cookies - keep them private."}
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    snap_id = await svc.db.execute(
        "INSERT INTO mini_app_snapshots(mini_app_id, path, mode, states, created_at) VALUES(?,?,?,?,?)",
        (app["id"], str(out), mode, sess.states, now_iso()))
    svc.bus.emit("miniapps.changed")
    return {"snapshot_id": snap_id, "states": sess.states, "path": str(out)}


UNSAFE_LINK = re.compile(r"pay|checkout|invoice|wallet|logout|log-out|signout|delete|remove|subscribe|buy|purchase|"
                         r"order|withdraw|transfer|donate|oplat|оплат|купи", re.I)


def _norm(url: str) -> str:
    return strip_init_data(url).rstrip("/")


async def _same_origin_links(page: Any) -> list[str]:
    return await page.evaluate(r"""() => {
      const out = new Set();
      for (const a of document.querySelectorAll('a[href]')) {
        const raw = a.getAttribute('href') || '';
        if (!raw || raw.startsWith('javascript:')) continue;
        let u; try { u = new URL(raw, location.href); } catch (e) { continue; }
        if (u.origin !== location.origin) continue;
        out.add(u.href);
      }
      return [...out];
    }""")


async def _crawl_links(page: Any, context: Any, sess: Session, ctx: JobContext, start_url: str, budget: int,
                       started: float, duration: int) -> int:
    """Visit every same-origin page link (GET navigation only, nothing is clicked). Returns pages visited."""
    visited = {_norm(start_url)}
    queue = [u for u in await _same_origin_links(page)]
    n = 0
    while queue and n < budget and not sess.stop and time.monotonic() - started < duration:
        await ctx.check()
        url = queue.pop(0)
        key = _norm(url)
        if key in visited or UNSAFE_LINK.search(url.split("#tgWebAppData")[0]):
            continue
        visited.add(key)
        try:
            # in-page navigation keeps the Telegram init data the app stored on first load
            await page.evaluate("u => { location.href = u }", url)
            await page.wait_for_load_state("domcontentloaded", timeout=15_000)
        except Exception:  # noqa: BLE001
            continue
        await asyncio.sleep(0.6)  # gentle pace
        n += 1
        await sess.snapshot(page, context, f"link {key.rsplit('/', 1)[-1] or '/'}")
        try:
            queue += [u for u in await _same_origin_links(page) if _norm(u) not in visited]
        except Exception:  # noqa: BLE001
            pass
        await ctx.progress(stage="links", states=sess.states, clicks=n, elapsed=int(time.monotonic() - started))
    return n


async def _auto_crawl(page: Any, context: Any, sess: Session, ctx: JobContext, start_url: str, max_depth: int,
                      max_clicks: int, started: float, duration: int) -> None:
    """Links first (cheap, safe), then a breadth-first walk over safe clickable elements; the click walk returns to
    the start URL and replays the path per click."""
    max_clicks -= await _crawl_links(page, context, sess, ctx, start_url, max_clicks, started, duration)
    queue: list[list[str]] = [[]]
    clicks = 0
    seen_paths: set[tuple[str, ...]] = set()
    while queue and clicks < max_clicks and not sess.stop and time.monotonic() - started < duration:
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
            await ctx.progress(stage="crawl", states=sess.states, clicks=clicks,
                               elapsed=int(time.monotonic() - started))


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


def build_app_site(app_dir: Path) -> dict[str, Any]:
    """Merge every saved session of one Mini App into app_dir/all_site.md + all_index.html (duplicates skipped)."""
    merged = Session(app_dir)
    seen: set[str] = set()
    for sd in sorted(p for p in app_dir.iterdir() if p.is_dir() and (p / "states").is_dir()):
        for md in sorted((sd / "states").glob("*.md")):
            raw = md.read_text(encoding="utf-8")
            lines = raw.split("\n")
            title = lines[0].removeprefix("# ").strip() if lines else md.stem
            url = next((x[2:].strip() for x in lines[1:4] if x.startswith("> ")), "")
            text = "\n".join(lines[4:]).strip() if len(lines) > 4 else ""
            key = hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()
            if not text or key in seen:
                continue
            seen.add(key)
            rel = f"{sd.name}/states/{md.stem}"
            merged.pages.append({"n": rel, "title": title, "label": sd.name, "url": url, "text": text})
    if merged.pages:
        merged.write_site()
        (app_dir / "site.md").replace(app_dir / "all_site.md")
        html = (app_dir / "index.html").read_text(encoding="utf-8")
        # links in the merged index point into each session folder
        html = re.sub(r'(href|src)="states/([^"]+)/states/', r'\1="\2/states/', html)
        html = html.replace('href="#s', 'href="#s').replace("Mini App - ", "Mini App (all sessions) - ", 1)
        (app_dir / "all_index.html").write_text(html, encoding="utf-8")
        (app_dir / "index.html").unlink()
    return {"pages": len(merged.pages), "path": str(app_dir / "all_index.html")}


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
        browser = await pw.chromium.launch(headless=False, args=await resolver_args(meta["url"]))
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
