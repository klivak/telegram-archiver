"""Entry point: `uv run tgarchiver` starts the local server on 127.0.0.1 and opens the UI."""

from __future__ import annotations

import argparse
import logging
import os
import secrets
import sys
import threading
import webbrowser
from pathlib import Path

from tgarchiver import APP_NAME, __version__


def find_static() -> Path | None:
    here = Path(__file__).resolve().parent
    candidates = [here / "web", here.parent.parent / "frontend" / "dist"]
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        candidates.insert(0, Path(sys.argv[0]).resolve().parent / "web")
    for c in candidates:
        if (c / "index.html").exists():
            return c
    return None


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="tgarchiver", description=f"{APP_NAME} {__version__}")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--no-browser", action="store_true", help="do not open the browser")
    ap.add_argument("--dev", action="store_true", help="dev mode: allow the Vite dev server origin")
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--version", action="version", version=__version__)
    args = ap.parse_args(argv)

    from tgarchiver.core.env import load_dotenv

    load_dotenv()  # before anything reads TGARCHIVER_* variables

    import uvicorn

    from tgarchiver.api.app import create_app
    from tgarchiver.core.config import load_settings
    from tgarchiver.core.logging import setup_logging
    from tgarchiver.core.paths import app_dir
    from tgarchiver.core.secrets import SecretStore
    from tgarchiver.services import Services

    setup_logging(app_dir() / "logs", logging.DEBUG if args.debug else logging.INFO)
    settings = load_settings()
    env_port = os.environ.get("TGARCHIVER_PORT", "")
    port = args.port or (int(env_port) if env_port.isdigit() else settings.port)
    token = os.environ.get("TGARCHIVER_TOKEN") or secrets.token_urlsafe(24)
    svc = Services(settings, SecretStore())
    static = find_static()
    dev_origins = ["http://localhost:5173", "http://127.0.0.1:5173"] if args.dev else None
    app = create_app(svc, token, static, dev_origins=dev_origins)

    url = f"http://127.0.0.1:{port}/"
    if args.dev:
        # Written for the Vite dev server (vite.config.ts reads it); the file lives in the private app dir.
        (app_dir() / "dev-token").write_text(token, encoding="utf-8")
        print(f"\n  {APP_NAME} API: {url}\n  Dev UI: http://localhost:5173/#token={token}\n", flush=True)
        open_url = f"http://localhost:5173/#token={token}"
    else:
        print(f"\n  {APP_NAME} {__version__}: {url}\n", flush=True)
        if static is None:
            print("  (frontend is not built - run scripts/build-frontend.ps1 or use --dev with `pnpm dev`)", flush=True)
        open_url = url
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(open_url)).start()
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
