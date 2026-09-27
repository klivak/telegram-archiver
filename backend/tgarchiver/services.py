"""Service container wiring config, DB, jobs, Telegram and feature modules together."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from tgarchiver.core.config import Settings, save_settings
from tgarchiver.core.db import Database, now_iso
from tgarchiver.core.events import EventBus
from tgarchiver.core.paths import safe_name
from tgarchiver.core.secrets import SecretStore
from tgarchiver.jobs.engine import JobEngine
from tgarchiver.tg.auth import AuthService
from tgarchiver.tg.client import TelegramService

log = logging.getLogger(__name__)


class Services:
    def __init__(self, settings: Settings, secrets: SecretStore, *, db_path: Path | None = None,
                 persist_settings: bool = True) -> None:
        self.settings = settings
        self.secrets = secrets
        self.persist_settings = persist_settings
        self.bus = EventBus()
        self.db = Database(db_path or settings.archive_path / "tgarchiver.db")
        self.engine = JobEngine(self.db, self.bus, max_concurrent=4, max_retries=settings.max_retries)
        self.tg = TelegramService(settings, secrets, self.bus)
        self.auth = AuthService(self.tg)
        self.account_slug = "default"
        self.background: list[Any] = []  # objects with async stop()
        self.whisper: Any = None  # transcribe.whisper.WhisperRunner, set by api.lifecycle
        self.monitor: Any = None  # monitor.service.MonitorService

    async def start(self) -> None:
        await self.db.open()
        acct = await self.db.kv_get("account")
        if acct:
            self.account_slug = acct.get("slug", "default")
        # Downloads interrupted by a crash/restart go back to the queue (.part files make them resume).
        await self.db.execute("UPDATE media SET status='pending' WHERE status='downloading'")
        from tgarchiver.jobs.registry import register_all

        register_all(self)
        self.auth.on_login.append(self._after_login)
        await self.engine.start()

    async def stop(self) -> None:
        for b in self.background:
            try:
                await b.stop()
            except Exception:  # noqa: BLE001
                log.exception("background stop failed")
        await self.auth.cancel_qr()
        await self.engine.stop()
        await self.tg.disconnect()
        await self.db.close()

    def save_settings(self) -> None:
        if self.persist_settings:
            save_settings(self.settings)

    @property
    def account_dir(self) -> Path:
        return self.settings.archive_path / self.account_slug

    async def _after_login(self) -> None:
        me = self.tg.me or {}
        slug = safe_name(me.get("username") or str(me.get("id") or "default"), max_len=40)
        self.account_slug = slug
        await self.db.kv_set("account", {"id": me.get("id"), "slug": slug})
        await self.db.execute(
            "INSERT INTO accounts(id, tg_user_id, phone, name, username, created_at) VALUES(1,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET tg_user_id=excluded.tg_user_id, phone=excluded.phone, "
            "name=excluded.name, username=excluded.username",
            (me.get("id"), me.get("phone"), " ".join(filter(None, [me.get("first_name"), me.get("last_name")])),
             me.get("username"), now_iso()),
        )
        # Show chats immediately after login: refresh dialogs in the background.
        await self.engine.submit("sync_dialogs", {}, title="sync_dialogs")
