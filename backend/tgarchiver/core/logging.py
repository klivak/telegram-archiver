"""Logging with a redaction filter: never leak sessions, initData or API keys."""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

_PATTERNS = [
    re.compile(r"(tgWebAppData=)[^&#\s]+", re.I),
    re.compile(r"(api[_-]?key[\"'=:\s]+)[\w\-.]+", re.I),
    re.compile(r"(bearer\s+)[\w\-.]+", re.I),
    re.compile(r"(token=)[\w\-.]+", re.I),
    re.compile(r"(sk-[a-zA-Z0-9_\-]{4})[a-zA-Z0-9_\-]+"),
    re.compile(r"\b(1[A-Za-z0-9_\-]{8})[A-Za-z0-9_\-+/=]{200,}"),  # StringSession-looking blobs
]

_EXC_FORMATTER = logging.Formatter()


def redact(text: str) -> str:
    for p in _PATTERNS:
        text = p.sub(r"\1***", text)
    return text


class RedactFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        red = redact(msg)
        if red != msg:
            record.msg, record.args = red, None
        # Pre-render tracebacks redacted so exception messages cannot leak secrets.
        if record.exc_info and not record.exc_text:
            record.exc_text = redact(_EXC_FORMATTER.formatException(record.exc_info))
        return True


def setup_logging(log_dir: Path, level: int = logging.INFO) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    fh = RotatingFileHandler(log_dir / "tgarchiver.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    sh = logging.StreamHandler()
    for h in (fh, sh):
        h.setFormatter(fmt)
        h.addFilter(RedactFilter())
        root.addHandler(h)
    logging.getLogger("telethon").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
