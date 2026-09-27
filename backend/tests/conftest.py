from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from telethon import errors
from telethon.tl import types

os.environ.setdefault("TGARCHIVER_HOME", str(Path(__file__).parent / ".tmp-home"))

from tgarchiver.core.config import Settings  # noqa: E402
from tgarchiver.core.secrets import MemorySecretStore  # noqa: E402
from tgarchiver.services import Services  # noqa: E402

ME = 1000
BASE = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)


def make_msg(mid: int, *, peer: Any = None, text: str = "hello", out: bool = False, from_id: int | None = 2000,
             days: int = 0, media: Any = None, **kw: Any) -> types.Message:
    return types.Message(
        id=mid, peer_id=peer or types.PeerUser(2000), date=BASE + timedelta(days=days, minutes=mid), message=text,
        out=out, from_id=types.PeerUser(from_id) if from_id else None, media=media, **kw)


def make_doc(doc_id: int, size: int, *, name: str = "file.bin", attrs: list[Any] | None = None,
             mime: str = "application/octet-stream") -> types.MessageMediaDocument:
    doc = types.Document(id=doc_id, access_hash=1, file_reference=b"ref", date=BASE, mime_type=mime, size=size,
                         dc_id=2, attributes=[types.DocumentAttributeFilename(name), *(attrs or [])])
    return types.MessageMediaDocument(document=doc)


class FakeClient:
    """Minimal Telethon stand-in: history, downloads with injectable failures."""

    def __init__(self, messages: list[types.Message] | None = None, file_bytes: dict[int, bytes] | None = None) -> None:
        self.messages = sorted(messages or [], key=lambda m: m.id)
        self.file_bytes = file_bytes or {}
        self.flood_once_at: int | None = None  # raise FloodWait when min_id == this
        self.fail_download_after: int | None = None  # chunks
        self.ref_expired_once: set[int] = set()
        self.download_calls: list[tuple[int, int]] = []
        self.get_calls = 0

    async def get_messages(self, peer: Any, limit: int | None = None, min_id: int = 0, reverse: bool = False,
                           ids: Any = None) -> Any:
        self.get_calls += 1
        if ids is not None:
            if isinstance(ids, list):
                return [next((m for m in self.messages if m.id == i), None) for i in ids]
            return next((m for m in self.messages if m.id == ids), None)
        if self.flood_once_at is not None and min_id == self.flood_once_at:
            self.flood_once_at = None
            raise errors.FloodWaitError(request=None, capture=3)
        items = [m for m in self.messages if m.id > min_id]
        if not reverse:
            items = list(reversed(items))
        res = _TotalList(items[: limit or len(items)])
        res.total = len(self.messages)
        return res

    async def iter_download(self, media: Any, offset: int = 0, request_size: int = 512 * 1024,
                            file_size: int | None = None) -> Any:
        doc_id = media.id
        if doc_id in self.ref_expired_once:
            self.ref_expired_once.discard(doc_id)
            raise errors.FileReferenceExpiredError(request=None)
        self.download_calls.append((doc_id, offset))
        data = self.file_bytes[doc_id]
        pos = offset
        n = 0
        while pos < len(data):
            if self.fail_download_after is not None and n >= self.fail_download_after:
                self.fail_download_after = None
                raise ConnectionError("network dropped")
            chunk = data[pos : pos + request_size]
            pos += len(chunk)
            n += 1
            yield chunk


class _TotalList(list):  # type: ignore[type-arg]
    total = 0


@pytest.fixture
async def svc(tmp_path: Path) -> AsyncIterator[Services]:
    settings = Settings(archive_root=str(tmp_path / "archive"), history_rps=1000, max_retries=3)
    s = Services(settings, MemorySecretStore(), persist_settings=False)
    s.engine.max_retries = 3
    await s.start()
    s.tg.me = {"id": ME, "first_name": "Me", "username": "me"}
    s.account_slug = "me"
    yield s
    await s.stop()


def use_client(s: Services, client: FakeClient) -> None:
    async def authorized() -> FakeClient:
        return client

    s.tg.authorized_client = authorized  # type: ignore[method-assign]


async def add_chat(s: Services, chat_id: int = 2000, title: str = "Alice", type_: str = "user", **kw: Any) -> None:
    cols = {"id": chat_id, "account_id": 1, "access_hash": 5, "type": type_, "title": title, "unread_count": 0,
            "read_inbox_max_id": 0, "noforwards": 0, **kw}
    keys = ",".join(cols)
    await s.db.execute(f"INSERT INTO chats({keys}) VALUES({','.join('?' * len(cols))})", list(cols.values()))
