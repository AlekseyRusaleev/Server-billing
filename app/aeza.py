"""Коннектор Aeza API (только чтение).

Док.: https://my.aeza.net/api/v2/docs#/
     https://github.com/AezaGroup/dev-docs
API-ключ: https://my.aeza.net/settings/apikeys
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from datetime import date, datetime, timezone

from app.connectors import ConnectorError, RemoteService

logger = logging.getLogger(__name__)

DEFAULT_API_BASE = "https://my.aeza.net/api"
LEGACY_API_BASE = "https://core.aeza.net/api"
CABINET_URL = "https://my.aeza.net/"

PAYMENT_TERM_DAYS = {
    "hour": 1,
    "day": 1,
    "week": 7,
    "month": 30,
    "mount": 30,
    "quarter_year": 90,
    "half_year": 180,
    "year": 365,
}

STATUS_MAP = {
    "active": "active",
    "activation_wait": "suspended",
    "activation": "suspended",
    "suspended": "suspended",
    "blocked": "suspended",
    "stopped": "suspended",
    "deleted": "deleted",
    "expired": "deleted",
    "cancelled": "deleted",
}


class AezaConnector:
    def __init__(self, api_key: str, api_base: str = DEFAULT_API_BASE, timeout: int = 25) -> None:
        self.api_key = (api_key or "").strip()
        self.api_base = _normalize_api_base(api_base)
        self.timeout = timeout
        if not self.api_key:
            raise ConnectorError("Не указан API-ключ Aeza.")

    def _request(self, path: str, *, method: str = "GET") -> dict[str, object]:
        url = f"{self.api_base}/{path.lstrip('/')}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "X-API-Key": self.api_key,
            "User-Agent": "server-billing-manager/1.0",
            "Accept": "application/json",
        }
        request = urllib.request.Request(url, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            detail = ""
            try:
                payload = json.loads(error.read().decode("utf-8"))
                if isinstance(payload, dict):
                    err = payload.get("error")
                    if isinstance(err, dict):
                        detail = str(err.get("message") or err.get("slug") or "").strip()
                    elif isinstance(err, str):
                        detail = err.strip()
            except (json.JSONDecodeError, OSError):
                detail = ""
            suffix = f": {detail}" if detail else ""
            if error.code in (401, 403):
                raise ConnectorError(
                    f"Aeza отклонил API-ключ (HTTP {error.code}){suffix}. "
                    "Создайте ключ в my.aeza.net → Настройки → API-ключи."
                ) from error
            raise ConnectorError(f"Aeza вернул HTTP {error.code}{suffix}.") from error
        except urllib.error.URLError as error:
            raise ConnectorError(f"Не удалось подключиться к Aeza: {error.reason}.") from error
        except json.JSONDecodeError as error:
            raise ConnectorError("Aeza вернул неожиданный ответ (не JSON).") from error

        if not isinstance(body, dict):
            raise ConnectorError("Aeza вернул неожиданный формат ответа.")
        err = body.get("error")
        if err:
            message = ""
            if isinstance(err, dict):
                message = str(err.get("message") or err.get("slug") or "").strip()
            elif isinstance(err, str):
                message = err.strip()
            raise ConnectorError(message or "Aeza отклонил запрос.")
        return body

    def test_connection(self) -> None:
        self._request("services?count=1")

    def list_services(self) -> list[RemoteService]:
        payload = self._request("services?count=100")
        data = payload.get("data")
        if not isinstance(data, dict):
            return []
        items = data.get("items")
        if not isinstance(items, list):
            return []

        services: list[RemoteService] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            service = _service_from_payload(item)
            if service is not None:
                services.append(service)
        return services


def _normalize_api_base(api_base: str) -> str:
    base = (api_base or DEFAULT_API_BASE).strip().rstrip("/")
    lowered = base.lower()
    if not base or "core.aeza.net" in lowered or base == LEGACY_API_BASE.rstrip("/"):
        return DEFAULT_API_BASE
    if lowered.endswith("/api/v2"):
        return base
    if lowered.endswith("/api"):
        return base
    if "aeza.net" in lowered:
        return f"{base}/api"
    return base


def _service_from_payload(item: dict[str, object]) -> RemoteService | None:
    service_id = item.get("id")
    if service_id is None:
        return None
    name = str(item.get("name") or "").strip()
    ip = str(item.get("ip") or "").strip()
    payment_term = str(item.get("paymentTerm") or "month").strip().lower()
    status = _map_status(str(item.get("status") or item.get("currentStatus") or "active"))
    location = str(item.get("locationCode") or "").strip().upper()
    expires_at = _parse_expires(item.get("timestamps"))
    amount = _price_from_service(item, payment_term)
    period_days = PAYMENT_TERM_DAYS.get(payment_term, 30)
    product = item.get("product")
    if isinstance(product, dict) and not name:
        name = str(product.get("name") or "").strip()
    service_url = f"{CABINET_URL.rstrip('/')}/services/{service_id}"
    return RemoteService(
        service_id=str(service_id),
        name=name or str(service_id),
        ip_address=ip,
        status=status,
        next_payment_date=expires_at,
        amount=amount,
        currency="EUR",
        payment_url=service_url,
        billing_period_days=period_days,
        location=location,
    )


def _map_status(raw: str) -> str:
    normalized = raw.strip().lower()
    return STATUS_MAP.get(normalized, "active" if normalized else "active")


def _parse_expires(timestamps: object) -> date | None:
    if not isinstance(timestamps, dict):
        return None
    raw = timestamps.get("expiresAt")
    if raw is None:
        return None
    try:
        if isinstance(raw, str):
            raw = raw.strip()
            if not raw:
                return None
            if raw.isdigit():
                ts = int(raw)
            else:
                normalized = raw.replace("Z", "+00:00")
                return datetime.fromisoformat(normalized).date()
        else:
            ts = int(raw)
    except (TypeError, ValueError):
        return None
    if ts <= 0:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).date()


def _price_from_service(item: dict[str, object], payment_term: str) -> float | None:
    for key in ("individualPrices", "rawPrices"):
        prices = item.get(key)
        if isinstance(prices, dict):
            amount = _price_from_map(prices, payment_term)
            if amount is not None:
                return amount
    product = item.get("product")
    if isinstance(product, dict):
        for key in ("individualPrices", "rawPrices"):
            prices = product.get(key)
            if isinstance(prices, dict):
                amount = _price_from_map(prices, payment_term)
                if amount is not None:
                    return amount
        prices = product.get("prices")
        if isinstance(prices, dict):
            for term_key, field in (
                (payment_term, payment_term),
                ("month", "month"),
                ("hour", "hour"),
            ):
                raw = prices.get(field) if field in prices else prices.get(term_key)
                amount = _coerce_price(raw)
                if amount is not None:
                    return amount
    return None


def _price_from_map(prices: dict[str, object], payment_term: str) -> float | None:
    for term in (payment_term, "month", "hour"):
        if term not in prices:
            continue
        amount = _coerce_price(prices.get(term))
        if amount is not None:
            return amount
    return None


def _coerce_price(raw: object) -> float | None:
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    # rawPrices у Aeza — в центах; prices у product — уже в евро.
    if value >= 100 and float(int(value)) == value:
        return round(value / 100, 2)
    return round(value, 2)
