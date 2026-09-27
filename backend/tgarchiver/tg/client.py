"""The single Telethon client per process: session in secure storage, manual FloodWait handling, rate limiter."""

from __future__ import annotations

import asyncio
import logging
import platform
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

from telethon import TelegramClient, errors
from telethon.sessions import StringSession

from tgarchiver import APP_NAME, __version__
from tgarchiver.core.config import Settings
from tgarchiver.core.events import EventBus
from tgarchiver.core.secrets import SecretStore
from tgarchiver.jobs.engine import FloodWait, RetryableError

log = logging.getLogger(__name__)
T = TypeVar("T")

SESSION_KEY = "session"
API_HASH_KEY = "api_hash"


class RateLimiter:
    """Token bucket shared by every history/API request of the account."""

    def __init__(self, rate: float, burst: int = 3, jitter: float = 0.5) -> None:
        self.rate = max(rate, 0.05)
        self.burst = burst
        self.jitter = jitter  # random extra pause (fraction of the base interval) so request timing is not robotic
        self.tokens = float(burst)
        self.updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self.tokens = min(self.burst, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    if self.jitter:
                        await asyncio.sleep(random.uniform(0, self.jitter / self.rate))
                    return
                await asyncio.sleep((1 - self.tokens) / self.rate)


class NotAuthorized(Exception):  # noqa: N818
    pass


class TelegramService:
    def __init__(self, settings: Settings, secrets: SecretStore, bus: EventBus) -> None:
        self.settings = settings
        self.secrets = secrets
        self.bus = bus
        self.client: TelegramClient | None = None
        self.limiter = RateLimiter(settings.history_rps)
        self.media_sem = asyncio.Semaphore(max(1, settings.media_concurrency))
        self.me: dict[str, Any] | None = None
        self._lock = asyncio.Lock()
        self.flood_until: float = 0.0  # monotonic; global pause for the media queue

    @property
    def api_hash(self) -> str | None:
        return self.secrets.get(API_HASH_KEY)

    @property
    def configured(self) -> bool:
        return bool(self.settings.api_id and self.api_hash)

    def _make_client(self, session: str) -> TelegramClient:
        assert self.settings.api_id and self.api_hash
        client = TelegramClient(
            StringSession(session),
            self.settings.api_id,
            self.api_hash,
            device_model=APP_NAME,
            app_version=__version__,
            system_version=f"{platform.system()} {platform.release()}",
            flood_sleep_threshold=0,  # never sleep silently; FloodWait surfaces to the UI
            request_retries=3,
            connection_retries=5,
            auto_reconnect=True,
        )
        return client

    async def get_client(self) -> TelegramClient:
        """Connected client (may be unauthorized, used by the login flow)."""
        async with self._lock:
            if self.client is None:
                if not self.configured:
                    raise NotAuthorized("api_id/api_hash are not configured")
                self.client = self._make_client(self.secrets.get(SESSION_KEY) or "")
            if not self.client.is_connected():
                await self.client.connect()
            return self.client

    async def authorized_client(self) -> TelegramClient:
        client = await self.get_client()
        if not await client.is_user_authorized():
            raise NotAuthorized("not logged in")
        return client

    async def is_authorized(self) -> bool:
        if not self.configured or not self.secrets.has(SESSION_KEY):
            return False
        try:
            client = await self.get_client()
            return bool(await client.is_user_authorized())
        except (OSError, ConnectionError, errors.RPCError):
            return self.secrets.has(SESSION_KEY)

    def save_session(self) -> None:
        if self.client is not None:
            s = self.client.session.save()  # type: ignore[union-attr]
            if s:
                self.secrets.set(SESSION_KEY, s)

    async def load_me(self) -> dict[str, Any] | None:
        client = await self.authorized_client()
        me = await client.get_me()
        if me is None:
            return None
        self.me = {
            "id": me.id,
            "first_name": me.first_name,
            "last_name": me.last_name,
            "username": me.username,
            "phone": me.phone,
        }
        return self.me

    async def reset(self) -> None:
        if self.client is not None:
            try:
                await self.client.disconnect()  # type: ignore[func-returns-value]
            except Exception:  # noqa: BLE001
                pass
        self.client = None
        self.me = None

    async def logout(self) -> None:
        try:
            client = await self.authorized_client()
            await client.log_out()
        except Exception:  # noqa: BLE001
            log.warning("log_out failed; removing local session anyway")
        self.secrets.delete(SESSION_KEY)
        await self.reset()

    async def disconnect(self) -> None:
        await self.reset()

    async def call(self, fn: Callable[[], Awaitable[T]], *, limited: bool = True) -> T:
        """Run a Telegram request with the shared limiter and FloodWait/transient error translation."""
        if limited:
            await self.limiter.acquire()
        try:
            return await fn()
        except errors.FloodWaitError as e:
            self.flood_until = time.monotonic() + e.seconds
            raise FloodWait(e.seconds) from e
        except errors.FloodPremiumWaitError as e:  # type: ignore[attr-defined]
            raise FloodWait(e.seconds) from e
        except errors.TakeoutInitDelayError as e:
            raise FloodWait(e.seconds, "takeout") from e
        except (errors.ServerError, errors.TimedOutError) as e:  # type: ignore[attr-defined]
            raise RetryableError(str(e)) from e
