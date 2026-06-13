"""Обёртка master-ключа шифрования паролем (DEK не хранится на диске открытым текстом)."""
from __future__ import annotations

import base64
import hashlib
import json
import os

from cryptography.fernet import Fernet, InvalidToken

KEY_WRAP_FORMAT = "sb-key-wrap-v1"
KEY_WRAP_ITERATIONS = 600_000


def _derive_fernet(passphrase: str, salt: bytes) -> Fernet:
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        passphrase.encode("utf-8"),
        salt,
        KEY_WRAP_ITERATIONS,
        dklen=32,
    )
    return Fernet(base64.urlsafe_b64encode(derived))


def wrap_encryption_key(dek: str, passphrase: str) -> bytes:
    if not dek.strip():
        raise ValueError("Пустой ключ шифрования.")
    if len(passphrase) < 12:
        raise ValueError("Пароль разблокировки должен быть не короче 12 символов.")
    salt = os.urandom(16)
    wrapped = _derive_fernet(passphrase, salt).encrypt(dek.strip().encode("utf-8"))
    payload = {
        "format": KEY_WRAP_FORMAT,
        "iterations": KEY_WRAP_ITERATIONS,
        "salt": base64.b64encode(salt).decode("ascii"),
        "wrapped": base64.b64encode(wrapped).decode("ascii"),
    }
    return (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")


def unwrap_encryption_key(payload: bytes, passphrase: str) -> str:
    try:
        data = json.loads(payload.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise ValueError("Некорректный файл обёртки ключа.") from error
    if data.get("format") != KEY_WRAP_FORMAT:
        raise ValueError("Неподдерживаемый формат обёртки ключа.")
    salt = base64.b64decode(data["salt"])
    wrapped = base64.b64decode(data["wrapped"])
    iterations = int(data.get("iterations") or KEY_WRAP_ITERATIONS)
    derived = hashlib.pbkdf2_hmac(
        "sha256",
        passphrase.encode("utf-8"),
        salt,
        iterations,
        dklen=32,
    )
    fernet = Fernet(base64.urlsafe_b64encode(derived))
    try:
        return fernet.decrypt(wrapped).decode("utf-8").strip()
    except InvalidToken as error:
        raise ValueError("Неверный пароль разблокировки ключа шифрования.") from error
