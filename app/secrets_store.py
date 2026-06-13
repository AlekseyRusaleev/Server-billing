"""Загрузка master-ключей с диска (отдельно от .env и data/)."""
from __future__ import annotations

import logging
from pathlib import Path

from app.config import settings
from app.key_wrap import unwrap_encryption_key

logger = logging.getLogger(__name__)

DEFAULT_ENCRYPTION_KEY_FILE = "secrets/encryption.key"
DEFAULT_ENCRYPTION_KEY_WRAP_FILE = "secrets/encryption.key.wrap"
DEFAULT_SESSION_KEY_FILE = "secrets/session.key"
DEFAULT_PASSPHRASE_SECRET = "/run/secrets/panel_key_passphrase"

_unlocked_encryption_key: str | None = None


def _read_key_file(path: str) -> str:
    cleaned = (path or "").strip()
    if not cleaned:
        return ""
    key_path = Path(cleaned)
    if not key_path.is_file():
        return ""
    return key_path.read_text(encoding="utf-8").strip()


def _read_passphrase() -> str:
    inline = settings.panel_key_passphrase.strip()
    if inline:
        return inline
    for candidate in (
        settings.panel_key_passphrase_file.strip(),
        DEFAULT_PASSPHRASE_SECRET,
        "secrets/unlock.passphrase",
    ):
        if candidate:
            text = _read_key_file(candidate)
            if text:
                return text
    return ""


def _wrap_file_path() -> Path:
    configured = settings.app_encryption_key_wrap_file.strip()
    return Path(configured or DEFAULT_ENCRYPTION_KEY_WRAP_FILE)


def _raw_encryption_key_path() -> Path:
    configured = settings.app_encryption_key_file.strip()
    return Path(configured or DEFAULT_ENCRYPTION_KEY_FILE)


def clear_encryption_key_cache() -> None:
    global _unlocked_encryption_key
    _unlocked_encryption_key = None


def encryption_key() -> str:
    global _unlocked_encryption_key
    if _unlocked_encryption_key:
        return _unlocked_encryption_key

    inline = settings.app_encryption_key.strip()
    if inline:
        return inline

    wrap_path = _wrap_file_path()
    if wrap_path.is_file():
        passphrase = _read_passphrase()
        if not passphrase:
            return ""
        try:
            _unlocked_encryption_key = unwrap_encryption_key(
                wrap_path.read_bytes(),
                passphrase,
            )
        except ValueError as error:
            logger.error("Не удалось разблокировать ключ шифрования: %s", error)
            return ""
        return _unlocked_encryption_key

    return _read_key_file(str(_raw_encryption_key_path()))


def session_secret_key() -> str:
    inline = settings.app_secret_key.strip()
    if inline:
        return inline
    file_path = settings.app_secret_key_file.strip() or DEFAULT_SESSION_KEY_FILE
    return _read_key_file(file_path)


def encryption_key_source() -> str:
    if settings.app_encryption_key.strip():
        return "env"
    if _wrap_file_path().is_file():
        if encryption_key():
            return "wrap"
        if _read_passphrase():
            return "wrap-locked"
        return "wrap-no-passphrase"
    if _read_key_file(str(_raw_encryption_key_path())):
        return "file"
    return "missing"


def session_secret_source() -> str:
    if settings.app_secret_key.strip():
        return "env"
    file_path = settings.app_secret_key_file.strip() or DEFAULT_SESSION_KEY_FILE
    if _read_key_file(file_path):
        return "file"
    return "missing"


def encryption_key_needs_passphrase() -> bool:
    return _wrap_file_path().is_file() and not _read_passphrase()


def encryption_key_is_legacy_raw_file() -> bool:
    return _raw_encryption_key_path().is_file() and not _wrap_file_path().is_file()
