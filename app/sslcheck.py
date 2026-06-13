"""Проверка срока действия SSL-сертификатов.

Цели проверки собираются из двух источников:
- поле `ssl_host` у серверов (по-серверно);
- отдельный список доменов в таблице `ssl_monitors`.

Сертификат читается без проверки цепочки (CERT_NONE), чтобы можно было узнать
срок даже у просроченного или самоподписанного серта. Дата окончания берётся
из самого сертификата через cryptography.
"""
from __future__ import annotations

import socket
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlparse

from cryptography import x509

from app.repository import list_servers, list_ssl_monitors, set_ssl_monitor_status
from app.url_safety import assert_public_host

DEFAULT_PORT = 443
CHECK_TIMEOUT = 6.0


def clean_host(raw: str) -> tuple[str, int]:
    """Возвращает (host, port) из строки вида example.com, https://example.com:8443/x."""
    value = (raw or "").strip()
    if not value:
        return "", DEFAULT_PORT
    if "://" not in value:
        value = "//" + value
    parsed = urlparse(value)
    host = parsed.hostname or ""
    port = parsed.port or DEFAULT_PORT
    return host, port


@dataclass
class SslResult:
    label: str
    host: str
    port: int
    source: str
    monitor_id: int | None = None
    ok: bool = False
    days_left: int | None = None
    expiry: str = ""
    error: str = ""


def check_ssl_expiry(host: str, port: int = DEFAULT_PORT, timeout: float = CHECK_TIMEOUT):
    """Возвращает (not_after: datetime, days_left: int). Бросает исключение при ошибке."""
    assert_public_host(host, context="SSL-хост")
    if port < 1 or port > 65535:
        raise ValueError("Некорректный порт SSL.")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=timeout) as sock:
        with context.wrap_socket(sock, server_hostname=host) as ssock:
            der = ssock.getpeercert(binary_form=True)
    certificate = x509.load_der_x509_certificate(der)
    not_after = certificate.not_valid_after_utc
    days_left = (not_after - datetime.now(timezone.utc)).days
    return not_after, days_left


def collect_targets() -> list[SslResult]:
    targets: list[SslResult] = []
    seen: set[tuple[str, int]] = set()
    for server in list_servers():
        host, port = clean_host(server.ssl_host)
        if not host or (host, port) in seen:
            continue
        seen.add((host, port))
        targets.append(SslResult(label=server.name, host=host, port=port, source="server"))
    for monitor in list_ssl_monitors():
        host, port = clean_host(str(monitor.get("host", "")))
        if not host:
            continue
        port = int(monitor.get("port") or port)
        if (host, port) in seen:
            continue
        seen.add((host, port))
        targets.append(
            SslResult(
                label=str(monitor.get("label") or host),
                host=host,
                port=port,
                source="monitor",
                monitor_id=int(monitor["id"]),
            )
        )
    return targets


def check_target(target: SslResult) -> SslResult:
    try:
        not_after, days_left = check_ssl_expiry(target.host, target.port)
        target.ok = True
        target.days_left = days_left
        target.expiry = not_after.strftime("%Y-%m-%d")
    except Exception as exc:  # noqa: BLE001 - показываем причину пользователю
        target.ok = False
        target.error = str(exc)
    if target.source == "monitor" and target.monitor_id is not None:
        set_ssl_monitor_status(
            target.monitor_id,
            "ok" if target.ok else "error",
            target.days_left,
            target.expiry,
        )
    return target


def run_all() -> list[SslResult]:
    return [check_target(target) for target in collect_targets()]
