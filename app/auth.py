from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

from fastapi import Request

from app.config import settings
from app.db import connect

COOKIE_NAME = "sb_session"
SESSION_VERSION_KEY = "session_version"
SESSION_TTL_SECONDS = 60 * 60 * 24 * 30
PBKDF2_ITERATIONS = 260_000


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PBKDF2_ITERATIONS,
    )
    return "pbkdf2_sha256:{}:{}:{}".format(
        PBKDF2_ITERATIONS,
        base64.urlsafe_b64encode(salt).decode("ascii"),
        base64.urlsafe_b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        separator = ":" if ":" in stored_hash else "$"
        algorithm, iterations, salt, digest = stored_hash.split(separator, 3)
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            base64.urlsafe_b64decode(salt.encode("ascii")),
            int(iterations),
        )
        expected = base64.urlsafe_b64decode(digest.encode("ascii"))
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def auth_enabled() -> bool:
    return bool(admin_password_hash().strip() and session_secret())


def session_secret() -> str:
    return session_secret_key()


def sign_payload(payload: dict[str, object]) -> str:
    body = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    signature = hmac.new(
        session_secret().encode("utf-8"),
        body.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return f"{body}.{signature}"


def current_session_version() -> int:
    try:
        with connect() as connection:
            row = connection.execute(
                "SELECT value FROM app_settings WHERE key = ?",
                (SESSION_VERSION_KEY,),
            ).fetchone()
        if row and row["value"]:
            return int(row["value"])
    except Exception:
        pass
    return 0


def bump_session_version() -> None:
    version = current_session_version() + 1
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO app_settings (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (SESSION_VERSION_KEY, str(version)),
        )


def verify_session_token(token: str) -> dict[str, object] | None:
    if not auth_enabled() or "." not in token:
        return None
    body, signature = token.rsplit(".", 1)
    expected = hmac.new(
        session_secret().encode("utf-8"),
        body.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(body.encode("ascii")))
    except Exception:
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    if int(payload.get("ver", -1)) != current_session_version():
        return None
    return payload


def create_session_token(username: str) -> str:
    return sign_payload(
        {
            "sub": username,
            "iat": int(time.time()),
            "exp": int(time.time()) + SESSION_TTL_SECONDS,
            "ver": current_session_version(),
            "nonce": secrets.token_urlsafe(12),
        }
    )


def is_authenticated(request: Request) -> bool:
    if not auth_enabled():
        return False
    token = request.cookies.get(COOKIE_NAME, "")
    payload = verify_session_token(token)
    return bool(payload and payload.get("sub") == settings.admin_username)


def auth_setup_message() -> str:
    missing: list[str] = []
    if not session_secret():
        missing.append("ключ сессии (secrets/session.key)")
    if not admin_password_hash().strip():
        missing.append("ADMIN_PASSWORD_HASH")
    if not missing:
        return ""
    return f"Панель заблокирована: задайте {', '.join(missing)} и перезапустите сервис."


_LOGIN_WINDOW_SECONDS = 15 * 60
_LOGIN_MAX_ATTEMPTS = 5
_login_failures: dict[str, list[float]] = {}


def login_rate_limited(client_ip: str) -> bool:
    now = time.time()
    attempts = [stamp for stamp in _login_failures.get(client_ip, []) if now - stamp < _LOGIN_WINDOW_SECONDS]
    _login_failures[client_ip] = attempts
    return len(attempts) >= _LOGIN_MAX_ATTEMPTS


def record_login_failure(client_ip: str) -> None:
    _login_failures.setdefault(client_ip, []).append(time.time())


def clear_login_failures(client_ip: str) -> None:
    _login_failures.pop(client_ip, None)


def check_login(username: str, password: str) -> bool:
    return (
        username == settings.admin_username
        and verify_password(password, admin_password_hash())
    )


def admin_password_hash() -> str:
    try:
        with connect() as connection:
            row = connection.execute(
                "SELECT value FROM app_settings WHERE key = 'admin_password_hash'"
            ).fetchone()
        if row and row["value"]:
            return row["value"]
    except Exception:
        pass
    return settings.admin_password_hash
