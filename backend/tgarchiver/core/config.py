"""Application settings persisted as JSON in the app dir."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from tgarchiver.core.paths import app_dir, default_archive_root


class WhisperSettings(BaseModel):
    enabled: bool = False
    model: str = "small"
    device: Literal["auto", "cpu", "cuda"] = "auto"
    compute_type: str = "auto"
    beam_size: int = 5
    language: str = "auto"
    auto: bool = False
    auto_chat_ids: list[int] = Field(default_factory=list)


class AISettings(BaseModel):
    enabled: bool = False
    provider: Literal["ollama", "anthropic", "openai", "openrouter", "groq", "gemini"] = "ollama"
    model: str = ""
    ollama_url: str = "http://127.0.0.1:11434"
    mask_pii: bool = True
    daily_token_limit: int = 200_000
    schedule_time: str = ""  # "09:00" -> daily digest; empty = off
    prompts: dict[str, str] = Field(default_factory=dict)  # user overrides of built-in templates


class MonitorSettings(BaseModel):
    enabled: bool = False
    interval_min: int = 15
    chat_ids: list[int] = Field(default_factory=list)  # empty = all
    folder_ids: list[int] = Field(default_factory=list)
    ignore_chat_ids: list[int] = Field(default_factory=list)
    mentions_only: bool = False
    mark_read_after: bool = False
    stay_offline: bool = True


class Settings(BaseModel):
    api_id: int | None = None
    archive_root: str = Field(default_factory=lambda: str(default_archive_root()))
    port: int = 8765
    language: Literal["uk", "en"] = "uk"
    theme: Literal["auto", "light", "dark"] = "auto"
    history_rps: float = 1.0
    media_concurrency: int = 3
    max_retries: int = 5
    use_takeout: bool = False  # Takeout needs a confirmation in the Telegram app; opt-in under Advanced
    protected_content: bool = True  # noforwards chats are archived by default; can be turned off in Settings
    import_tg_folders: bool = True
    path_template: str = "{type}/{yyyy}-{mm}/{msgid}_{name}"
    download_window: str = ""  # "01:00-07:00" or empty
    daily_gb_limit: float = 0.0
    check_updates: bool = True
    last_update_check: str = ""
    tutorial_done: bool = False
    advanced_mode: bool = False
    whisper: WhisperSettings = Field(default_factory=WhisperSettings)
    ai: AISettings = Field(default_factory=AISettings)
    monitor: MonitorSettings = Field(default_factory=MonitorSettings)

    @property
    def archive_path(self) -> Path:
        return Path(self.archive_root)


def config_path() -> Path:
    return app_dir() / "config.json"


def load_settings() -> Settings:
    p = config_path()
    s = Settings()
    if p.exists():
        try:
            s = Settings.model_validate_json(p.read_text(encoding="utf-8"))
        except ValueError:
            p.replace(p.with_suffix(".broken.json"))
    env_api_id = os.environ.get("TGARCHIVER_API_ID", "").strip()
    if s.api_id is None and env_api_id.isdigit():
        s.api_id = int(env_api_id)
    if os.environ.get("TGARCHIVER_ARCHIVE_ROOT") and not p.exists():
        s.archive_root = os.environ["TGARCHIVER_ARCHIVE_ROOT"]
    return s


def save_settings(s: Settings) -> None:
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(s.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(p)
