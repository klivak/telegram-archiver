from __future__ import annotations

import asyncio

from tgarchiver.jobs.engine import Deferred, FloodWait, JobContext, RetryableError
from tgarchiver.services import Services


async def test_demo_job_runs(svc: Services) -> None:
    jid = await svc.engine.submit("demo", {"steps": 3, "delay": 0.01})
    job = await svc.engine.wait(jid)
    assert job["status"] == "done"
    assert job["progress"]["done"] == 3
    assert job["progress"]["result"] == {"steps": 3}


async def test_pause_resume_continues_from_checkpoint(svc: Services) -> None:
    jid = await svc.engine.submit("demo", {"steps": 30, "delay": 0.02})
    for _ in range(200):
        j = await svc.engine.get(jid)
        if j and j["progress"].get("done", 0) >= 3:
            break
        await asyncio.sleep(0.01)
    await svc.engine.pause(jid)
    job = await svc.engine.wait(jid)
    assert job["status"] == "paused"
    reached = job["progress"]["done"]
    assert 3 <= reached < 30
    await svc.engine.resume(jid)
    job = await svc.engine.wait(jid)
    assert job["status"] == "done"
    ck = await svc.db.fetchone("SELECT checkpoint FROM jobs WHERE id=?", (jid,))
    assert '"step": 30' in ck["checkpoint"]


async def test_cancel(svc: Services) -> None:
    jid = await svc.engine.submit("demo", {"steps": 100, "delay": 0.02})
    await asyncio.sleep(0.1)
    await svc.engine.cancel(jid)
    job = await svc.engine.wait(jid)
    assert job["status"] == "cancelled"


async def test_flood_wait_is_respected_and_auto_resumes(svc: Services, monkeypatch) -> None:
    from tgarchiver.jobs import engine as engine_mod

    monkeypatch.setattr(engine_mod, "jitter", lambda s, **_: s)  # the random margin is tested separately
    calls: list[float] = []

    async def flaky(ctx: JobContext) -> str:
        calls.append(asyncio.get_running_loop().time())
        if len(calls) == 1:
            raise FloodWait(1)
        return "ok"

    svc.engine.register("flaky", flaky)
    jid = await svc.engine.submit("flaky", {})
    job = await svc.engine.wait(jid)
    assert job["status"] == "flood_wait"
    assert job["wait_until"]
    for _ in range(100):
        job = await svc.engine.get(jid)
        if job and job["status"] == "done":
            break
        await asyncio.sleep(0.1)
    assert job and job["status"] == "done"
    assert calls[1] - calls[0] >= 1.0, "must not retry before the flood wait elapses"


async def test_transient_errors_retry_then_fail(svc: Services) -> None:
    async def broken(ctx: JobContext) -> None:
        raise RetryableError("net")

    svc.engine.register("broken", broken)
    jid = await svc.engine.submit("broken", {})
    for _ in range(150):
        job = await svc.engine.get(jid)
        if job and job["status"] == "failed":
            break
        await asyncio.sleep(0.1)
    assert job and job["status"] == "failed"
    assert job["attempts"] == svc.engine.max_retries + 1
    await svc.engine.retry(jid)
    job = await svc.engine.get(jid)
    assert job and job["status"] in ("queued", "running", "failed")


async def test_deferred_requeues_with_wait(svc: Services) -> None:
    async def later(ctx: JobContext) -> None:
        raise Deferred(3600, "download_window")

    svc.engine.register("later", later)
    jid = await svc.engine.submit("later", {})
    for _ in range(50):
        job = await svc.engine.get(jid)
        if job and job["progress"].get("deferred"):
            break
        await asyncio.sleep(0.05)
    assert job and job["status"] == "queued" and job["wait_until"]


async def test_restart_requeues_running(svc: Services) -> None:
    jid = await svc.engine.submit("demo", {"steps": 100, "delay": 0.05})
    await asyncio.sleep(0.2)
    await svc.engine.stop()
    job = await svc.engine.get(jid)
    assert job and job["status"] == "queued"
    await svc.engine.start()
    await svc.engine.cancel(jid)


def test_jitter_only_lengthens_and_varies() -> None:
    from tgarchiver.jobs.engine import jitter

    vals = [jitter(30, spread=0.1, extra=10) for _ in range(200)]
    assert all(30 <= v <= 30 + 3 + 10 for v in vals)
    assert len({round(v, 3) for v in vals}) > 50
