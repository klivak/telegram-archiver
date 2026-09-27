from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tgarchiver.api.app import create_app
from tgarchiver.core.config import Settings
from tgarchiver.core.secrets import MemorySecretStore
from tgarchiver.services import Services

TOKEN = "t0k"


@pytest.fixture
def ctx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    opened: list[list[str]] = []
    monkeypatch.setattr(subprocess, "Popen", lambda args, *a, **k: opened.append(list(args)))
    svc = Services(Settings(archive_root=str(tmp_path / "a")), MemorySecretStore(), persist_settings=False)
    with TestClient(create_app(svc, TOKEN)) as c:
        c.headers["Authorization"] = f"Bearer {TOKEN}"
        yield c, svc, opened


def test_reveal_archive_and_missing_paths(ctx) -> None:
    c, svc, opened = ctx
    r = c.post("/api/archive/reveal")
    assert r.status_code == 200 and Path(r.json()["path"]).exists()
    # a file that is not downloaded yet opens the closest existing folder inside the archive
    deep = svc.settings.archive_path / "me" / "chats" / "X" / "media" / "photo" / "1.jpg"
    r = c.post("/api/open-path", json={"path": str(deep)})
    assert r.status_code == 200 and Path(r.json()["path"]) == svc.settings.archive_path.resolve()
    assert c.post("/api/open-path", json={"path": "C:/Windows"}).status_code == 403
    assert len(opened) == 2


def test_export_estimate_reports_all_types(ctx) -> None:
    c, svc, _ = ctx
    r = c.post("/api/export/estimate", json={"chat_ids": [1], "media_types": ["photo"], "no_media": False})
    assert r.status_code == 200
    body = r.json()
    assert "all_types" in body and "messages_total" in body


def test_whisper_status_and_test_endpoint(ctx, monkeypatch) -> None:
    import tgarchiver.transcribe.whisper as wh

    c, svc, _ = ctx
    r = c.get("/api/whisper/status")
    assert r.status_code == 200 and {"installed", "cuda_devices", "models", "voice_done"} <= r.json().keys()
    monkeypatch.setattr(wh, "available", lambda: True)
    r = c.post("/api/whisper/test")
    assert r.status_code == 400 and r.json()["detail"]["code"] == "no_voice_downloaded"


async def test_miniapp_resolver_args(monkeypatch) -> None:
    from tgarchiver.miniapps import recorder

    async def fake(host: str) -> str | None:
        return "1.2.3.4" if host == "app.pages.dev" else None

    monkeypatch.setattr(recorder, "doh_resolve", fake)
    assert await recorder.resolver_args("https://app.pages.dev/#tgWebAppData=x") == [
        "--host-resolver-rules=MAP app.pages.dev 1.2.3.4"]
    assert await recorder.resolver_args("https://other.example/") == []
    assert await recorder.resolver_args("http://10.0.0.1/") == []
