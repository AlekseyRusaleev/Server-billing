"""Проверка URL для форм и исходящих запросов коннекторов."""
from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

from fastapi import HTTPException

from app.connectors import ConnectorError

_BLOCKED_NETWORKS = (
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
)


def validate_http_url(url: str, *, field: str = "URL", allow_empty: bool = True) -> str:
    cleaned = (url or "").strip()
    if not cleaned:
        if allow_empty:
            return ""
        raise HTTPException(status_code=400, detail=f"{field}: укажите адрес.")
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(
            status_code=400,
            detail=f"{field}: разрешены только ссылки http:// или https://.",
        )
    if not parsed.netloc or not parsed.hostname:
        raise HTTPException(status_code=400, detail=f"{field}: некорректный адрес.")
    return cleaned


def _ip_is_blocked(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if addr.is_loopback or addr.is_link_local or addr.is_multicast or addr.is_reserved:
        return True
    return any(addr in network for network in _BLOCKED_NETWORKS)


def _resolve_host_ips(hostname: str) -> list[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    lowered = hostname.strip().lower().rstrip(".")
    if lowered in {"localhost"}:
        return [ipaddress.ip_address("127.0.0.1")]
    try:
        literal = ipaddress.ip_address(lowered)
        return [literal]
    except ValueError:
        pass
    ips: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    try:
        for info in socket.getaddrinfo(lowered, None, type=socket.SOCK_STREAM):
            ips.append(ipaddress.ip_address(info[4][0]))
    except socket.gaierror as error:
        raise ConnectorError(f"Не удалось разрешить имя хоста {hostname}: {error}.") from error
    if not ips:
        raise ConnectorError(f"Не удалось разрешить имя хоста {hostname}.")
    return ips


def assert_public_http_url(url: str, *, context: str = "URL") -> str:
    cleaned = validate_http_url(url, field=context, allow_empty=False)
    hostname = urlparse(cleaned).hostname or ""
    for addr in _resolve_host_ips(hostname):
        if _ip_is_blocked(addr):
            raise ConnectorError(
                f"{context}: запрещён адрес {hostname} ({addr}) — private/link-local/metadata."
            )
    return cleaned


def assert_public_host(host: str, *, context: str = "хост") -> str:
    cleaned = (host or "").strip().rstrip(".")
    if not cleaned:
        raise ConnectorError(f"{context}: укажите имя хоста.")
    for addr in _resolve_host_ips(cleaned):
        if _ip_is_blocked(addr):
            raise ConnectorError(
                f"{context}: запрещён адрес {cleaned} ({addr}) — private/link-local/metadata."
            )
    return cleaned


def assert_host_suffix(url: str, allowed_suffixes: tuple[str, ...], *, context: str = "URL") -> str:
    cleaned = assert_public_http_url(url, context=context)
    hostname = (urlparse(cleaned).hostname or "").lower()
    allowed = tuple(item.lower().lstrip(".") for item in allowed_suffixes if item)
    if not any(hostname == suffix or hostname.endswith(f".{suffix}") for suffix in allowed):
        raise ConnectorError(
            f"{context}: хост {hostname} не входит в разрешённые домены ({', '.join(allowed)})."
        )
    return cleaned
