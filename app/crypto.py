from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.secrets_store import encryption_key

FERNET_PREFIX = "fernet:"


class EncryptionRequiredError(RuntimeError):
    """Секрет нельзя сохранить без APP_ENCRYPTION_KEY."""


def cipher() -> Fernet | None:
    key = encryption_key()
    if not key:
        return None
    return Fernet(key.encode("utf-8"))


def encryption_configured() -> bool:
    return cipher() is not None


def encrypt_secret(value: str) -> str:
    if not value or value.startswith(FERNET_PREFIX):
        return value
    active_cipher = cipher()
    if active_cipher is None:
        raise EncryptionRequiredError(
            "Задайте ключ шифрования (secrets/encryption.key или APP_ENCRYPTION_KEY_FILE) "
            "перед сохранением паролей и API-ключей."
        )
    token = active_cipher.encrypt(value.encode("utf-8")).decode("utf-8")
    return FERNET_PREFIX + token


def encrypt_bytes(value: bytes) -> bytes:
    active_cipher = cipher()
    if active_cipher is None:
        raise EncryptionRequiredError(
            "Задайте ключ шифрования (secrets/encryption.key) для шифрования резервных копий."
        )
    return active_cipher.encrypt(value)


def decrypt_secret(value: str) -> str:
    if not value:
        return ""
    if not value.startswith(FERNET_PREFIX):
        return value
    active_cipher = cipher()
    if active_cipher is None:
        return ""
    token = value.removeprefix(FERNET_PREFIX).encode("utf-8")
    try:
        return active_cipher.decrypt(token).decode("utf-8")
    except InvalidToken:
        return ""


def is_encrypted(value: str) -> bool:
    return bool(value and value.startswith(FERNET_PREFIX))
