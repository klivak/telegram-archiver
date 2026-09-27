from pathlib import Path

import pytest

from tgarchiver.core.config import load_settings
from tgarchiver.core.env import load_dotenv, parse_env
from tgarchiver.core.secrets import MemorySecretStore, SecretStore


def test_parse_env() -> None:
    text = '# c\nA=1\nexport B="x y"\nC=\'q\'\nD=v # note\n\nbad line\nE='
    assert parse_env(text) == {"A": "1", "B": "x y", "C": "q", "D": "v", "E": ""}


def test_load_dotenv_does_not_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = tmp_path / ".env"
    f.write_text("TGA_T1=from_file\nTGA_T2=file2\n", encoding="utf-8")
    monkeypatch.setenv("TGA_T1", "preset")
    monkeypatch.delenv("TGA_T2", raising=False)
    assert load_dotenv([f, tmp_path / "missing.env"]) == [f.resolve()]
    import os

    assert os.environ["TGA_T1"] == "preset"
    assert os.environ["TGA_T2"] == "file2"
    monkeypatch.delenv("TGA_T2")


def test_secret_env_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    import keyring

    monkeypatch.setattr(keyring, "get_password", lambda *_: None)
    monkeypatch.setenv("TGARCHIVER_API_HASH", "a" * 32)
    assert SecretStore("t").get("api_hash") == "a" * 32
    assert SecretStore("t").get("unknown") is None
    assert MemorySecretStore().get("api_hash") is None  # tests stay isolated from env


def test_api_id_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import tgarchiver.core.config as cfg

    monkeypatch.setattr(cfg, "config_path", lambda: tmp_path / "config.json")
    monkeypatch.setenv("TGARCHIVER_API_ID", "12345")
    assert load_settings().api_id == 12345
    (tmp_path / "config.json").write_text('{"api_id": 7}', encoding="utf-8")
    assert load_settings().api_id == 7  # config.json wins over env
