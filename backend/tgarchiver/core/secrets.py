"""Secret storage via the OS keyring (Windows Credential Manager/DPAPI, macOS Keychain, Secret Service).

Never log values returned from here.
"""

from __future__ import annotations

import os

import keyring
import keyring.errors

from tgarchiver.core.env import env_secret

# TGARCHIVER_KEYRING isolates a test/second instance from the real account (TGARCHIVER_HOME alone does not).
SERVICE = os.environ.get("TGARCHIVER_KEYRING") or "TelegramArchiver"
# Windows Credential Manager caps a blob at ~2.5KB; split long values into chunks.
_CHUNK = 1200


class SecretStore:
    def __init__(self, service: str = SERVICE) -> None:
        self.service = service

    def get(self, key: str) -> str | None:
        value = self._get_keyring(key)
        return value if value is not None else env_secret(key)

    def _get_keyring(self, key: str) -> str | None:
        try:
            head = keyring.get_password(self.service, key)
            if head is None or not head.startswith("chunks:"):
                return head
            n = int(head.split(":", 1)[1])
            return "".join(keyring.get_password(self.service, f"{key}#{i}") or "" for i in range(n))
        except keyring.errors.KeyringError:
            return None

    def set(self, key: str, value: str) -> None:
        self.delete(key)
        if len(value) <= _CHUNK:
            keyring.set_password(self.service, key, value)
            return
        parts = [value[i : i + _CHUNK] for i in range(0, len(value), _CHUNK)]
        for i, part in enumerate(parts):
            keyring.set_password(self.service, f"{key}#{i}", part)
        keyring.set_password(self.service, key, f"chunks:{len(parts)}")

    def delete(self, key: str) -> None:
        try:
            head = keyring.get_password(self.service, key)
            if head and head.startswith("chunks:"):
                for i in range(int(head.split(":", 1)[1])):
                    try:
                        keyring.delete_password(self.service, f"{key}#{i}")
                    except keyring.errors.PasswordDeleteError:
                        pass
            if head is not None:
                keyring.delete_password(self.service, key)
        except keyring.errors.KeyringError:
            pass

    def has(self, key: str) -> bool:
        return self.get(key) is not None


class MemorySecretStore(SecretStore):
    """In-memory store for tests."""

    def __init__(self) -> None:
        super().__init__("test")
        self._d: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self._d.get(key)

    def set(self, key: str, value: str) -> None:
        self._d[key] = value

    def delete(self, key: str) -> None:
        self._d.pop(key, None)
