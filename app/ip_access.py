from __future__ import annotations

import functools
import ipaddress

from fastapi import Request, WebSocket

from app.config import settings
from app.repository import get_app_setting

ALLOWLIST_KEY = "panel_ip_allowlist"

LOCALHOST_NETWORKS = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
)

DEFAULT_TRUSTED_PROXY_CIDRS = (
    "127.0.0.0/8",
    "::1/128",
)


def panel_ip_allowlist_text() -> str:
    return get_app_setting(ALLOWLIST_KEY, settings.panel_ip_allowlist or "").strip()


def _parse_ip(addr: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    text = addr.strip()
    if not text:
        return None
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        return ip.ipv4_mapped
    return ip


@functools.lru_cache(maxsize=1)
def _trusted_proxy_rules() -> tuple[
    tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...],
    tuple[ipaddress.IPv4Address | ipaddress.IPv6Address, ...],
]:
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    items = list(DEFAULT_TRUSTED_PROXY_CIDRS)
    extra = settings.trusted_proxies.strip()
    if extra:
        items.extend(
            item.strip()
            for item in extra.replace(",", "\n").split("\n")
            if item.strip()
        )
    for item in items:
        try:
            if "/" in item:
                networks.append(ipaddress.ip_network(item, strict=False))
            else:
                parsed = _parse_ip(item)
                if parsed is not None:
                    addresses.append(parsed)
        except ValueError:
            continue
    return tuple(networks), tuple(addresses)


def _is_trusted_proxy(addr_text: str) -> bool:
    ip = _parse_ip(addr_text)
    if ip is None:
        return False
    networks, addresses = _trusted_proxy_rules()
    if any(ip in network for network in networks):
        return True
    return any(ip == address for address in addresses)


def _peer_ip(peer_host: str | None) -> str:
    return peer_host.strip() if peer_host else ""


def _header(headers, name: str) -> str:
    return headers.get(name, "").strip()


def client_ip_from_peer_and_headers(peer_host: str | None, headers) -> str:
    peer = _peer_ip(peer_host)
    if not peer:
        return ""
    if not _is_trusted_proxy(peer):
        return peer

    forwarded = _header(headers, "x-forwarded-for")
    if forwarded:
        parts = [part.strip() for part in forwarded.split(",") if part.strip()]
        for part in reversed(parts):
            ip = _parse_ip(part)
            if ip is None:
                continue
            if not _is_trusted_proxy(str(ip)):
                return str(ip)
        if parts:
            ip = _parse_ip(parts[0])
            if ip is not None:
                return str(ip)

    real_ip = _header(headers, "x-real-ip")
    if real_ip:
        ip = _parse_ip(real_ip)
        if ip is not None:
            return str(ip)

    return peer


def client_ip(request: Request) -> str:
    peer = request.client.host if request.client else ""
    return client_ip_from_peer_and_headers(peer, request.headers)


def client_ip_from_websocket(websocket: WebSocket) -> str:
    peer = websocket.client.host if websocket.client else ""
    return client_ip_from_peer_and_headers(peer, websocket.headers)


def parse_allowlist(text: str) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network | ipaddress.IPv4Address | ipaddress.IPv6Address]:
    entries: list[
        ipaddress.IPv4Network | ipaddress.IPv6Network | ipaddress.IPv4Address | ipaddress.IPv6Address
    ] = []
    for raw in text.replace(",", "\n").split("\n"):
        item = raw.strip()
        if not item or item.startswith("#"):
            continue
        if "/" in item:
            entries.append(ipaddress.ip_network(item, strict=False))
        else:
            entries.append(ipaddress.ip_address(item))
    return entries


def normalize_allowlist(text: str) -> tuple[str, list[str]]:
    errors: list[str] = []
    lines: list[str] = []
    for raw in text.replace(",", "\n").split("\n"):
        item = raw.strip()
        if not item or item.startswith("#"):
            continue
        try:
            if "/" in item:
                ipaddress.ip_network(item, strict=False)
            else:
                ipaddress.ip_address(item)
        except ValueError:
            errors.append(item)
            continue
        lines.append(item)
    return "\n".join(lines), errors


def is_localhost(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return any(addr in network for network in LOCALHOST_NETWORKS)


def is_address_allowed(addr_text: str, allowlist_text: str | None = None) -> bool:
    text = panel_ip_allowlist_text() if allowlist_text is None else allowlist_text.strip()
    if not text:
        return True
    if not addr_text:
        return False
    try:
        addr = ipaddress.ip_address(addr_text)
    except ValueError:
        return False
    if is_localhost(addr):
        return True
    for entry in parse_allowlist(text):
        if isinstance(entry, (ipaddress.IPv4Network, ipaddress.IPv6Network)):
            if addr in entry:
                return True
        elif addr == entry:
            return True
    return False


def is_ip_allowed(request: Request) -> bool:
    return is_address_allowed(client_ip(request))


def is_websocket_ip_allowed(websocket: WebSocket) -> bool:
    return is_address_allowed(client_ip_from_websocket(websocket))
