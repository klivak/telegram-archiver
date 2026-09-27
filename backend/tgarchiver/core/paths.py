"""Filesystem helpers: app dirs and Windows-safe file names."""

from __future__ import annotations

import os
import re
import sys
import unicodedata
from pathlib import Path

from tgarchiver import APP_SLUG

_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
MAX_PATH = 240


def app_dir() -> Path:
    override = os.environ.get("TGARCHIVER_HOME")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / APP_SLUG


def local_data_dir() -> Path:
    """Heavy downloadable assets (Whisper models, browsers)."""
    override = os.environ.get("TGARCHIVER_HOME")
    if override:
        return Path(override) / "local"
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / APP_SLUG
    return app_dir() / "data"


def default_archive_root() -> Path:
    override = os.environ.get("TGARCHIVER_HOME")
    if override:
        return Path(override) / "archive"
    return Path.home() / "Documents" / APP_SLUG


def safe_name(name: str, max_len: int = 80, fallback: str = "untitled") -> str:
    """Make a string safe to use as a single path component on Windows."""
    name = unicodedata.normalize("NFC", name or "")
    name = _FORBIDDEN.sub("_", name)
    name = re.sub(r"\s+", " ", name).strip().rstrip(". ")
    if not name:
        name = fallback
    if name.split(".")[0].upper() in _RESERVED:
        name = "_" + name
    if len(name) > max_len:
        stem, dot, ext = name.rpartition(".")
        if dot and 0 < len(ext) <= 10 and stem:
            name = stem[: max_len - len(ext) - 1].rstrip(". ") + "." + ext
        else:
            name = name[:max_len].rstrip(". ")
    return name


def slug(title: str, max_len: int = 40) -> str:
    return safe_name(title, max_len=max_len, fallback="chat").replace(" ", "_")


def long_path(p: Path) -> Path:
    """Add the \\\\?\\ prefix on Windows for paths that exceed MAX_PATH."""
    if sys.platform == "win32" and len(str(p)) >= MAX_PATH and not str(p).startswith("\\\\?\\"):
        return Path("\\\\?\\" + str(p.resolve()))
    return p
