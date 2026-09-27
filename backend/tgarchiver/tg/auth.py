"""Login flows: QR (default), phone + code, 2FA password. Results are pushed over WS as auth.* events."""

from __future__ import annotations

import asyncio
import io
import logging
from datetime import UTC, datetime
from typing import Any

import qrcode
import qrcode.image.svg
from telethon import errors

from tgarchiver.tg.client import NotAuthorized, TelegramService

log = logging.getLogger(__name__)


class AuthError(Exception):
    """Error with a stable code the UI maps to a human message (docs/03)."""

    def __init__(self, code: str, seconds: int = 0) -> None:
        super().__init__(code)
        self.code = code
        self.seconds = seconds


def qr_svg(data: str) -> str:
    img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode("utf-8")


def _map_error(e: Exception) -> AuthError:
    if isinstance(e, errors.FloodWaitError):
        return AuthError("flood_wait", e.seconds)
    if isinstance(e, AuthError):
        return e
    if isinstance(e, NotAuthorized):
        return AuthError("need_config")
    table: list[tuple[type[Exception], str]] = [
        (errors.PhoneCodeInvalidError, "code_invalid"),
        (errors.PhoneCodeEmptyError, "code_invalid"),
        (errors.PhoneCodeExpiredError, "code_expired"),
        (errors.PhoneNumberUnoccupiedError, "phone_unoccupied"),
        (errors.PhoneNumberFloodError, "phone_flood"),
        (errors.PasswordHashInvalidError, "password_invalid"),
        (errors.PhoneNumberInvalidError, "phone_invalid"),
        (errors.PhoneNumberBannedError, "phone_banned"),
        (errors.ApiIdInvalidError, "api_id_invalid"),
        (errors.AuthKeyUnregisteredError, "session_revoked"),
        (ConnectionError, "network"),
        (OSError, "network"),
    ]
    for cls, code in table:
        if isinstance(e, cls):
            return AuthError(code)
    return AuthError("unknown")


class AuthService:
    def __init__(self, tg: TelegramService) -> None:
        self.tg = tg
        self.state = "idle"  # idle|qr|code_sent|password|ready
        self._qr_task: asyncio.Task[Any] | None = None
        self._phone: str | None = None
        self._phone_code_hash: str | None = None
        self.password_hint: str | None = None
        self.on_login: list[Any] = []  # async callbacks after successful login

    async def status(self) -> dict[str, Any]:
        configured = self.tg.configured
        authorized = await self.tg.is_authorized() if configured else False
        if authorized and self.tg.me is None:
            try:
                await self.tg.load_me()
            except Exception:  # noqa: BLE001
                pass
        if authorized:
            self.state = "ready"
        return {
            "configured": configured,
            "api_id": self.tg.settings.api_id,
            "authorized": authorized,
            "state": self.state if configured else "need_config",
            "me": self.tg.me if authorized else None,
            "password_hint": self.password_hint,
        }

    async def _finish(self) -> None:
        self.tg.save_session()
        me = await self.tg.load_me()
        self.state = "ready"
        self.tg.bus.emit("auth.ready", {"me": me})
        for cb in self.on_login:
            try:
                await cb()
            except Exception:  # noqa: BLE001
                log.exception("post-login hook failed")

    # ---------- QR ----------
    async def start_qr(self) -> dict[str, Any]:
        await self.cancel_qr()
        try:
            client = await self.tg.get_client()
            qr = await client.qr_login()
        except Exception as e:  # noqa: BLE001
            raise _map_error(e) from e
        self.state = "qr"
        payload = self._qr_payload(qr)
        self._qr_task = asyncio.create_task(self._qr_loop(qr), name="qr-login")
        return payload

    def _qr_payload(self, qr: Any) -> dict[str, Any]:
        return {"url": qr.url, "svg": qr_svg(qr.url), "expires": qr.expires.isoformat()}

    async def _qr_loop(self, qr: Any) -> None:
        while True:
            timeout = max(5.0, (qr.expires - datetime.now(UTC)).total_seconds())
            try:
                await qr.wait(timeout=timeout)
                await self._finish()
                return
            except TimeoutError:
                try:
                    await qr.recreate()
                except Exception as e:  # noqa: BLE001
                    err = _map_error(e)
                    self.tg.bus.emit("auth.error", {"code": err.code, "seconds": err.seconds})
                    return
                self.tg.bus.emit("auth.qr", self._qr_payload(qr))
            except errors.SessionPasswordNeededError:
                await self._need_password()
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                err = _map_error(e)
                self.tg.bus.emit("auth.error", {"code": err.code, "seconds": err.seconds})
                return

    async def cancel_qr(self) -> None:
        if self._qr_task and not self._qr_task.done():
            self._qr_task.cancel()
            try:
                await self._qr_task
            except BaseException:  # noqa: BLE001
                pass
        self._qr_task = None

    async def _need_password(self) -> None:
        self.state = "password"
        try:
            client = await self.tg.get_client()
            from telethon.tl.functions.account import GetPasswordRequest

            pwd = await client(GetPasswordRequest())
            self.password_hint = pwd.hint
        except Exception:  # noqa: BLE001
            self.password_hint = None
        self.tg.bus.emit("auth.password_needed", {"hint": self.password_hint})

    # ---------- phone ----------
    async def send_code(self, phone: str) -> dict[str, Any]:
        await self.cancel_qr()
        try:
            client = await self.tg.get_client()
            sent = await client.send_code_request(phone)
        except Exception as e:  # noqa: BLE001
            raise _map_error(e) from e
        self._phone = phone
        self._phone_code_hash = sent.phone_code_hash
        self.state = "code_sent"
        return {"state": self.state}

    async def verify_code(self, code: str) -> dict[str, Any]:
        if not self._phone:
            raise AuthError("no_phone")
        try:
            client = await self.tg.get_client()
            await client.sign_in(phone=self._phone, code=code, phone_code_hash=self._phone_code_hash)
        except errors.SessionPasswordNeededError:
            await self._need_password()
            return {"state": "password", "hint": self.password_hint}
        except Exception as e:  # noqa: BLE001
            raise _map_error(e) from e
        try:
            await self._finish()
        except Exception as e:  # noqa: BLE001
            raise _map_error(e) from e
        return {"state": "ready"}

    async def password(self, password: str) -> dict[str, Any]:
        if self.state != "password":
            raise AuthError("no_phone")  # e.g. the server restarted mid-login: start again
        try:
            client = await self.tg.get_client()
            await client.sign_in(password=password)
            await self._finish()
        except Exception as e:  # noqa: BLE001
            raise _map_error(e) from e
        return {"state": "ready"}

    async def logout(self) -> None:
        await self.cancel_qr()
        await self.tg.logout()
        self.state = "idle"
