"""Optional `.env` support for developers/headless setups (no extra dependency).

The keyring stays the primary store; env values are only a fallback. Never log values read here.
"""

from __future__ import annotations

import os
from pathlib import Path

# Secret key in SecretStore -> environment variable that may provide it.
SECRET_ENV = {
    "api_hash": "TGARCHIVER_API_HASH",
    "session": "TGARCHIVER_SESSION",
    "ai_key_anthropic": "ANTHROPIC_API_KEY",
    "ai_key_openai": "OPENAI_API_KEY",
    "ai_key_openrouter": "OPENROUTER_API_KEY",
    "ai_key_groq": "GROQ_API_KEY",
    "ai_key_gemini": "GEMINI_API_KEY",
}


def parse_env(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.removeprefix("export ").partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        if key:
            out[key] = value
    return out


def load_dotenv(paths: list[Path] | None = None) -> list[Path]:
    """Load `.env` files into os.environ without overriding variables that are already set."""
    if paths is None:
        here = Path(__file__).resolve()
        paths = [Path.cwd() / ".env", here.parents[3] / ".env", here.parents[2] / ".env"]
    loaded: list[Path] = []
    seen: set[Path] = set()
    for p in paths:
        p = p.resolve()
        if p in seen or not p.is_file():
            continue
        seen.add(p)
        for k, v in parse_env(p.read_text(encoding="utf-8-sig")).items():
            os.environ.setdefault(k, v)
        loaded.append(p)
    return loaded


def env_secret(key: str) -> str | None:
    name = SECRET_ENV.get(key)
    return (os.environ.get(name) or None) if name else None
