"""FastAPI app: token-protected REST + /ws/events, serves the built frontend (docs/12)."""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from tgarchiver import __version__
from tgarchiver.services import Services

log = logging.getLogger(__name__)
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1", "testserver"}


class HostGuard(BaseHTTPMiddleware):
    """Blocks DNS-rebinding: only loopback Host headers are accepted."""

    async def dispatch(self, request: Request, call_next: Any) -> Any:
        if request.url.hostname not in ALLOWED_HOSTS:
            return JSONResponse({"detail": "forbidden host"}, status_code=403)
        return await call_next(request)


def create_app(svc: Services, token: str, static_dir: Path | None = None, *, manage_lifecycle: bool = True,
               dev_origins: list[str] | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if manage_lifecycle:
            await svc.start()
            from tgarchiver.api.lifecycle import start_background

            await start_background(svc)
        try:
            yield
        finally:
            if manage_lifecycle:
                await svc.stop()

    app = FastAPI(title="Telegram Archiver", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.svc = svc
    app.state.token = token
    app.add_middleware(HostGuard)
    if dev_origins:
        from fastapi.middleware.cors import CORSMiddleware

        app.add_middleware(CORSMiddleware, allow_origins=dev_origins, allow_methods=["*"], allow_headers=["*"])

    def check_token(request: Request) -> None:
        auth = request.headers.get("authorization", "")
        given = auth[7:] if auth.lower().startswith("bearer ") else request.query_params.get("token", "")
        if not given or not hmac.compare_digest(given, token):
            raise HTTPException(401, "unauthorized")

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "version": __version__}

    from tgarchiver.api.routes import router

    app.include_router(router, prefix="/api", dependencies=[Depends(check_token)])

    @app.websocket("/ws/events")
    async def ws_events(ws: WebSocket) -> None:
        if ws.url.hostname not in ALLOWED_HOSTS or not hmac.compare_digest(ws.query_params.get("token", ""), token):
            await ws.close(code=4401)
            return
        await ws.accept()
        q = svc.bus.subscribe()
        try:
            await ws.send_text(json.dumps({"type": "hello", "data": {"version": __version__}}))
            while True:
                # Batch events (docs/17: throttle WS 200-500 ms).
                ev = await q.get()
                batch = [ev]
                await asyncio.sleep(0.2)
                while not q.empty() and len(batch) < 200:
                    batch.append(q.get_nowait())
                await ws.send_text(json.dumps({"type": "batch", "data": batch}, default=str))
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            svc.bus.unsubscribe(q)

    if static_dir and (static_dir / "index.html").exists():
        index_file = static_dir / "index.html"
        cache: dict[str, Any] = {"mtime": None, "html": ""}

        def injected() -> str:
            # Re-read after a frontend rebuild, otherwise the page would reference deleted hashed assets.
            mtime = index_file.stat().st_mtime
            if cache["mtime"] != mtime:
                html = index_file.read_text(encoding="utf-8")
                cache["html"] = html.replace("<head>", f'<head><script>window.__TGA_TOKEN__="{token}"</script>', 1)
                cache["mtime"] = mtime
            return str(cache["html"])

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str) -> Any:
            if path.startswith(("api/", "ws/", "assets/")) and not (static_dir / path).is_file():
                raise HTTPException(404)  # a missing hashed asset must not get index.html (wrong MIME, blank page)
            f = (static_dir / path).resolve()
            if path and f.is_file() and static_dir.resolve() in f.parents:
                return FileResponse(f)
            return HTMLResponse(injected(), headers={"Cache-Control": "no-store"})

    return app
