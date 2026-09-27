from __future__ import annotations

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tgarchiver.ai import pipeline
from tgarchiver.ai.pipeline import chunk, mask_pii, parse_json
from tgarchiver.ai.providers import LLMResult
from tgarchiver.api.app import create_app
from tgarchiver.core.config import Settings
from tgarchiver.core.secrets import MemorySecretStore
from tgarchiver.services import Services

TOKEN = "t0k"


@pytest.fixture
def client(tmp_path: Path):
    svc = Services(Settings(archive_root=str(tmp_path / "a")), MemorySecretStore(), persist_settings=False)
    app = create_app(svc, TOKEN)
    with TestClient(app) as c:
        c.headers["Authorization"] = f"Bearer {TOKEN}"
        yield c


def test_auth_required_and_host_guard(client: TestClient) -> None:
    assert client.get("/api/health").json()["ok"]
    assert client.get("/api/settings", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/api/settings", headers={"Host": "evil.example"}).status_code == 403
    assert client.get(f"/api/settings?token={TOKEN}", headers={"Authorization": ""}).status_code == 200


def test_auth_status_and_config(client: TestClient) -> None:
    st = client.get("/api/auth/status").json()
    assert st["configured"] is False and st["state"] == "need_config"
    assert client.post("/api/auth/config", json={"api_id": 1, "api_hash": "bad"}).status_code == 422
    r = client.post("/api/auth/config", json={"api_id": 123, "api_hash": "a" * 32})
    assert r.status_code == 200 and r.json()["configured"] is True


def test_settings_patch(client: TestClient) -> None:
    s = client.put("/api/settings", json={"language": "en", "ai": {"enabled": True}}).json()
    assert s["language"] == "en" and s["ai"]["enabled"] is True and s["ai"]["provider"] == "ollama"
    assert client.put("/api/settings", json={"language": "xx"}).status_code == 422
    assert client.post("/api/settings/secret", json={"key": "ai_key_openai", "value": "sk-1"}).json()["set"]
    assert client.get("/api/settings").json()["secrets"]["ai_key_openai"] is True


def test_demo_job_and_ws(client: TestClient) -> None:
    with client.websocket_connect(f"/ws/events?token={TOKEN}") as ws:
        assert ws.receive_json()["type"] == "hello"
        jid = client.post("/api/jobs-demo", json={"steps": 2}).json()["job_id"]
        seen = set()
        deadline = time.time() + 10
        while time.time() < deadline and "done" not in seen:
            msg = ws.receive_json()
            for ev in msg["data"]:
                if ev["type"] == "job.update" and ev["data"]["id"] == jid:
                    seen.add(ev["data"]["status"])
        assert "done" in seen
    assert client.get(f"/api/jobs/{jid}").json()["status"] == "done"


def test_ws_rejects_bad_token(client: TestClient) -> None:
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect), client.websocket_connect("/ws/events?token=bad") as ws:
        ws.receive_json()


def test_crud_folders_presets_and_lists(client: TestClient) -> None:
    fid = client.post("/api/folders", json={"name": "Work", "emoji": "💼"}).json()["id"]
    sub = client.post("/api/folders", json={"name": "Sub", "parent_id": fid}).json()["id"]
    assert client.post("/api/folders", json={"name": "Too deep", "parent_id": sub}).status_code == 400
    assert len(client.get("/api/folders").json()["items"]) == 2
    pid = client.post("/api/presets", json={"kind": "export", "name": "Docs", "data": {"media_types": ["document"]}})
    assert pid.status_code == 200
    assert client.get("/api/presets?kind=export").json()["items"][0]["data"]["media_types"] == ["document"]
    assert client.get("/api/chats").json()["items"] == []
    assert client.get("/api/search?q=hello").json()["total"] == 0
    assert client.get("/api/dashboard").json()["stats"]["chats"] == 0
    assert client.post("/api/open-path", json={"path": "C:/Windows"}).status_code == 403
    assert client.get("/api/archive-file", params={"path": "C:/Windows/win.ini"}).status_code == 404


def test_pii_and_json_parsing() -> None:
    t = mask_pii("call +380 67 123 45 67 or mail a.b@c.io, card 4111 1111 1111 1111")
    assert "[phone]" in t and "[email]" in t and "[card]" in t and "4111" not in t
    assert parse_json('noise {"overall": "x", "chats": []} tail', "digest")["overall"] == "x"
    with pytest.raises(ValueError):
        parse_json("no json", "digest")
    parts = chunk([("A", "x" * 400)] * 10, budget=250)
    assert len(parts) >= 5 and parts[0].startswith("\n## A") or parts[0].startswith("## A")


async def test_ai_job_with_fake_provider(svc: Services, monkeypatch) -> None:
    from conftest import add_chat

    await add_chat(svc, unread_count=2, read_inbox_max_id=0)
    await svc.db.execute("INSERT INTO messages(chat_id, id, date, text, out) VALUES(2000, 1, '2026-01-01', "
                         "'Can you send the report by Friday? my mail x@y.z', 0)")

    seen: list[str] = []

    class Fake:
        name, model = "fake", "m"

        async def complete(self, system: str, user: str, *, max_tokens: int = 2000) -> LLMResult:
            seen.append(user)
            return LLMResult('{"items": [{"task": "send report", "chat": "Alice", "due": "Friday"}]}', 10, 5)

    monkeypatch.setattr(pipeline, "make_provider", lambda *a, **k: Fake())
    jid = await svc.engine.submit("ai", {"task": "action_items", "scope": {"unread": True}})
    job = await svc.engine.wait(jid)
    assert job["status"] == "done", job
    assert "x@y.z" not in seen[0] and "[email]" in seen[0]
    rep = await svc.db.fetchone("SELECT * FROM ai_reports")
    assert "send report" in rep["result"] and rep["tokens_in"] == 10
    assert list((svc.account_dir / "ai").rglob("action_items_*.md"))


async def test_ai_period_scope_and_voice_pretranscribe(svc: Services, monkeypatch) -> None:
    from conftest import add_chat

    await add_chat(svc)
    for mid, date, text in [(1, "2026-01-01T10:00:00", "old"), (2, "2026-01-05T23:59:00", "in range"),
                            (3, "2026-01-06T08:00:00", ""), (4, "2026-01-09T00:00:00", "late")]:
        await svc.db.execute("INSERT INTO messages(chat_id, id, date, text, out) VALUES(2000, ?, ?, ?, 0)",
                             (mid, date, text))
    await svc.db.execute("UPDATE messages SET media_type='voice' WHERE chat_id=2000 AND id=3")
    await svc.db.execute("INSERT INTO media(chat_id, message_id, type, status) VALUES(2000, 3, 'voice', 'done')")
    scope = {"chat_ids": [2000], "since": "2026-01-05", "until": "2026-01-06"}
    lines = [line for _, line in await pipeline.collect_lines(svc, scope)]
    assert len(lines) == 2 and "in range" in lines[0] and "[voice]" in lines[1]
    media_ids = await pipeline.untranscribed_media(svc, scope)
    assert len(media_ids) == 1
    assert (await pipeline.estimate_job_tokens(svc, scope))["untranscribed_voice"] == 1

    import tgarchiver.transcribe.whisper as wh

    async def fake_transcribe(svc_: Services, ctx, params=None):
        assert params == {"media_ids": media_ids}
        await svc_.db.execute("INSERT INTO transcripts(media_id, lang, model, text) VALUES(?, 'uk', 'x', 'hello voice')",
                              (media_ids[0],))
        return {"transcribed": 1}

    seen: list[str] = []

    class Fake:
        name, model = "fake", "m"

        async def complete(self, system: str, user: str, *, max_tokens: int = 2000) -> LLMResult:
            seen.append(user)
            return LLMResult('{"summary": "ok", "chats": [], "sources": []}', 1, 1)

    monkeypatch.setattr(wh, "transcribe_job", fake_transcribe)
    monkeypatch.setattr(wh, "available", lambda: True)
    monkeypatch.setattr(pipeline, "make_provider", lambda *a, **k: Fake())
    svc.settings.whisper.enabled = True
    jid = await svc.engine.submit("ai", {"task": "digest", "scope": scope, "transcribe_voice": True})
    job = await svc.engine.wait(jid)
    assert job["status"] == "done", job
    assert seen and "hello voice" in seen[0]
