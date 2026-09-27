from __future__ import annotations

from tgarchiver.core.db import Database
from tgarchiver.core.logging import redact
from tgarchiver.core.paths import safe_name
from tgarchiver.miniapps.recorder import is_safe
from tgarchiver.miniapps.service import strip_init_data


def test_safe_name_windows() -> None:
    assert safe_name('a<b>c:"d/e\\f|g?h*') == "a_b_c__d_e_f_g_h_"
    assert safe_name("CON") == "_CON"
    assert safe_name("  trailing dots... ") == "trailing dots"
    assert safe_name("") == "untitled"
    long = safe_name("x" * 300 + ".mp4", max_len=50)
    assert len(long) <= 50 and long.endswith(".mp4")


async def test_migrations_and_fts(tmp_path) -> None:
    db = Database(tmp_path / "t.db")
    await db.open()
    assert await db.scalar("PRAGMA user_version") == 2
    assert await db.scalar("PRAGMA journal_mode") == "wal"
    await db.execute("INSERT INTO messages(chat_id, id, text, date) VALUES(1, 1, 'Привіт світ', '2026-01-01')")
    await db.execute("INSERT INTO media(chat_id, message_id, file_name, type) VALUES(1, 1, 'report.pdf', 'document')")
    await db.execute("UPDATE messages SET text='Привіт всесвіт' WHERE chat_id=1 AND id=1")
    assert await db.scalar("SELECT count(*) FROM messages_fts WHERE messages_fts MATCH '\"всесвіт\"'") == 1
    assert await db.scalar("SELECT count(*) FROM messages_fts WHERE messages_fts MATCH '\"report\"'") == 1
    # reopening is idempotent
    await db.close()
    await db.open()
    assert await db.scalar("PRAGMA user_version") == 2
    await db.close()


def test_redaction() -> None:
    assert "secret" not in redact("url https://x.y/#tgWebAppData=secret&a=1")
    assert "abcdef123" not in redact("api_key=abcdef123")
    assert "zzz" not in redact("Authorization: Bearer zzz")
    session = "1" + "A" * 300
    assert "A" * 50 not in redact(f"session {session}")


def test_strip_init_data() -> None:
    url = "https://app.example/?a=1#tgWebAppData=query_id%3D1&tgWebAppVersion=7"
    out = strip_init_data(url)
    assert "tgWebAppData" not in out and "tgWebAppVersion=7" in out


def test_crawler_never_clicks_payments() -> None:
    for text in ("Pay 5 ⭐", "Buy now", "Confirm", "Підтвердити", "Купити", "Withdraw", "Send", "Delete account",
                 "Оплатить", "Connect wallet"):
        assert not is_safe({"text": text, "href": ""}), text
    assert not is_safe({"text": "Open", "href": "https://t.me/$invoice"})
    assert is_safe({"text": "Profile", "href": "/profile"})


def test_redaction_covers_tracebacks() -> None:
    import io
    import logging

    from tgarchiver.core.logging import RedactFilter

    buf = io.StringIO()
    h = logging.StreamHandler(buf)
    h.addFilter(RedactFilter())
    lg = logging.getLogger("tga-redact-test")
    lg.addHandler(h)
    lg.propagate = False
    try:
        raise RuntimeError("bad url ?token=supersecret1")
    except RuntimeError:
        lg.exception("boom")
    finally:
        lg.removeHandler(h)
    out = buf.getvalue()
    assert "RuntimeError" in out and "supersecret1" not in out
