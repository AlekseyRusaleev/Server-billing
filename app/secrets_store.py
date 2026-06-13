"""Загрузка master-ключей с диска (отдельно от .env и data/)."""
from __future__ import annotations

from pathlib import Path

from app.config import settings

DEFAULT_ENCRYPTION_KEY_FILE = "secrets/encryption.key"
DEFAULT_SESSION_KEY_FILE = "secrets/session.key"


def _read_key_file(path: str) -> str:
    cleaned = (path or "").strip()
    if not cleaned:
        return ""
    key_path = Path(cleaned)
    if not key_path.is_file():
        return ""
    return key_path.read_text(encoding="utf-8").strip()


def encryption_key() -> str:
    inline = settings.app_encryption_key.strip()
    if inline:
        return inline
    file_path = settings.app_encryption_key_file.strip() or DEFAULT_ENCRYPTION_KEY_FILE
    return _read_key_file(file_path)


def session_secret_key() -> str:
    inline = settings.app_secret_key.strip()
    if inline:
        return inline
    file_path = settings.app_secret_key_file.strip() or DEFAULT_SESSION_KEY_FILE
    return _read_key_file(file_path)


def encryption_key_source() -> str:
    if settings.app_encryption_key.strip():
        return "env"
    file_path = settings.app_encryption_key_file.strip() or DEFAULT_ENCRYPTION_KEY_FILE
    if _read_key_file(file_path):
        return "file"
    return "missing"


def session_secret_source() -> str:
    if settings.app_secret_key.strip():
        return "env"
    file_path = settings.app_secret_key_file.strip() or DEFAULT_SESSION_KEY_FILE
    if _read_key_file(file_path):
        return "file"
    return "missing"
