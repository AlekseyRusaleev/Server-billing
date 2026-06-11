"""Коннектор OneDash.RDP Web API (только чтение).

Док.: https://github.com/OneDashRDP/api-docs
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from datetime import datetime

from app.connectors import ConnectorError, RemoteService

logger = logging.getLogger(__name__)

DEFAULT_API_BASE = "https://rdp-onedash.ru/web-api"
CABINET_URL = "https://rdp-onedash.ru/cabinet"

LOCATION_LABELS = {
    "msk": "Москва",
    "ams": "Амстердам",
    "hel": "Хельсинки",
    "fra": "Франкфурт",
}
LOCATION_CODES = {
    "msk": "RU",
    "ams": "NL",
    "hel": "FI",
    "fra": "DE",
}
LOCATION_ALIASES = {
    "ru": "msk",
    "nl": "ams",
    "fi": "hel",
    "de": "fra",
}

VPS_STATUS_MAP = {
    "runned": "active",
    "not_runned": "suspended",
    "cloning": "suspended",
}


class OneDashConnector:
    def __init__(self, api_key: str, api_base: str = DEFAULT_API_BASE, timeout: int = 25) -> None:
        self.api_key = (api_key or "").strip()
        base = (api_base or DEFAULT_API_BASE).strip().rstrip("/")
        self.api_base = base if base.endswith("/web-api") else f"{base.rstrip('/')}/web-api"
        self.timeout = timeout
        if not self.api_key:
            raise ConnectorError("Не указан Api-Key OneDash.")

    def _request(self, method: str, *, post: bool = False, payload: dict[str, object] | None = None) -> dict[str, object]:
        url = f"{self.api_base}/{method.lstrip('/')}"
        headers = {
            "Api-Key": self.api_key,
            "User-Agent": "server-billing-manager/1.0",
        }
        data = None
        if post:
            headers["Content-Type"] = "application/json"
            data = json.dumps(payload or {}).encode("utf-8")
        request = urllib.request.Request(url, data=data, method="POST" if post else "GET", headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            raise ConnectorError(f"OneDash вернул HTTP {error.code}.") from error
        except urllib.error.URLError as error:
            raise ConnectorError(f"Не удалось подключиться к OneDash: {error.reason}.") from error
        except json.JSONDecodeError as error:
            raise ConnectorError("OneDash вернул неожиданный ответ (не JSON).") from error

        if not isinstance(body, dict):
            raise ConnectorError("OneDash вернул неожиданный формат ответа.")
        if body.get("type") is False:
            raise ConnectorError("OneDash отклонил запрос (type=false). Проверьте Api-Key.")
        return body

    def test_connection(self) -> None:
        self._request("test-request")
        balance = self._request("balance")
        if not isinstance(balance.get("data"), dict):
            logger.info("OneDash balance response without data block.")

    def list_services(self) -> list[RemoteService]:
        tariffs = _load_tariffs(self._request("tariffs"))
        payload = self._request("all-orders")
        orders = payload.get("data")
        if not isinstance(orders, list):
            return []

        services: list[RemoteService] = []
        for order in orders:
            if not isinstance(order, dict):
                continue
            order_id = order.get("order_id")
            if order_id is not None:
                try:
                    info = self._request("order-info", post=True, payload={"order_id": order_id})
                    data = info.get("data")
                    if isinstance(data, dict):
                        order = {**order, **data}
                except ConnectorError:
                    logger.debug("OneDash order-info failed for order %s", order_id)
            services.extend(_services_from_order(order, tariffs))
        return services


def _load_tariffs(payload: dict[str, object]) -> dict[int, dict[str, object]]:
    rows = payload.get("data")
    if not isinstance(rows, list):
        return {}
    tariffs: dict[int, dict[str, object]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            tariff_id = int(row.get("id"))
        except (TypeError, ValueError):
            continue
        tariffs[tariff_id] = row
    return tariffs


def _effective_price(price_row: dict[str, object]) -> float:
    price = float(price_row.get("price") or 0)
    discount = float(price_row.get("discount") or 0)
    if discount > 0:
        return price * (1 - discount / 100)
    return price


def _normalize_location(location: str) -> str:
    key = location.strip().lower()
    return LOCATION_ALIASES.get(key, key)


def _price_bucket(prices: object, currency: str = "RUB") -> list[dict[str, object]]:
    """OneDash отдаёт цены списком или словарём локалей (ru/en/eu)."""
    if isinstance(prices, list):
        return [row for row in prices if isinstance(row, dict)]
    if isinstance(prices, dict):
        currency_key = currency.strip().lower()
        for key in (currency_key, "ru", "rub", "en", "eu"):
            bucket = prices.get(key)
            if isinstance(bucket, list):
                return [row for row in bucket if isinstance(row, dict)]
        for value in prices.values():
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
    return []


def _location_prices(tariff: dict[str, object], location: str) -> list[dict[str, object]]:
    loc = _normalize_location(location)
    currency = str(tariff.get("currency") or "RUB")
    direct_key = f"{loc}_prices"
    direct = tariff.get(direct_key)
    if direct is not None:
        bucket = _price_bucket(direct, currency)
        if bucket:
            return bucket

    for key, value in tariff.items():
        if not isinstance(key, str) or not key.endswith("_prices"):
            continue
        prefix = key[: -len("_prices")]
        if prefix != loc:
            continue
        bucket = _price_bucket(value, currency)
        if bucket:
            return bucket
    return []


def _parse_period(raw: object) -> int | None:
    if raw is None:
        return None
    try:
        period = int(raw)
    except (TypeError, ValueError):
        return None
    return period if 7 <= period <= 360 else None


def _parse_amount(raw: object) -> float | None:
    if raw is None:
        return None
    try:
        amount = float(raw)
    except (TypeError, ValueError):
        return None
    return amount if amount > 0 else None


def _order_period(order: dict[str, object]) -> int | None:
    for key in ("period", "rent_period", "billing_period", "renew_period", "pay_period"):
        period = _parse_period(order.get(key))
        if period is not None:
            return period
    for nested_key in ("payment", "renew", "billing", "price_info"):
        nested = order.get(nested_key)
        if isinstance(nested, dict):
            for key in ("period", "rent_period", "billing_period", "renew_period"):
                period = _parse_period(nested.get(key))
                if period is not None:
                    return period
    finish = order.get("finish_time")
    if isinstance(finish, dict):
        for key in ("period", "rent_period", "billing_period"):
            period = _parse_period(finish.get(key))
            if period is not None:
                return period
    return None


def _order_amount_from_payload(order: dict[str, object]) -> tuple[float | None, int | None, str]:
    currency = str(order.get("currency") or "RUB")
    period = _order_period(order)
    for key in (
        "renew_price",
        "renewal_price",
        "next_payment",
        "next_payment_price",
        "price",
        "amount",
        "payment_amount",
        "summ",
        "sum",
    ):
        amount = _parse_amount(order.get(key))
        if amount is not None:
            return amount, period, currency
    for nested_key in ("payment", "renew", "billing", "price_info"):
        nested = order.get(nested_key)
        if not isinstance(nested, dict):
            continue
        nested_currency = str(nested.get("currency") or currency)
        nested_period = _order_period(nested) or period
        for key in ("renew_price", "renewal_price", "price", "amount", "payment_amount", "summ", "sum"):
            amount = _parse_amount(nested.get(key))
            if amount is not None:
                return amount, nested_period, nested_currency
    return None, period, currency


def _price_rows(prices: list[dict[str, object]]) -> list[tuple[int, float]]:
    normalized: list[tuple[int, float]] = []
    for row in prices:
        period = _parse_period(row.get("period"))
        amount = _parse_amount(row.get("price"))
        if period is None or amount is None:
            continue
        normalized.append((period, _effective_price(row)))
    return normalized


def _renewal_amount(
    order: dict[str, object],
    tariffs: dict[int, dict[str, object]],
    tariff_id: int,
    location: str,
) -> tuple[float | None, int, str]:
    direct_amount, direct_period, direct_currency = _order_amount_from_payload(order)
    if direct_amount is not None:
        return direct_amount, direct_period or 30, direct_currency

    tariff = tariffs.get(tariff_id)
    if not tariff:
        return None, 30, "RUB"

    currency = str(tariff.get("currency") or "RUB")
    prices = _price_rows(_location_prices(tariff, location))
    if not prices:
        logger.warning(
            "OneDash: нет цен для тарифа %s в локации %s (order %s).",
            tariff_id,
            location,
            order.get("order_id"),
        )
        return None, 30, currency

    preferred_period = _order_period(order)
    if preferred_period is not None:
        for period, price in prices:
            if period == preferred_period:
                return price, period, currency

    for target in (7, 10, 14, 30, 60, 90, 180, 360):
        for period, price in prices:
            if period == target:
                return price, period, currency

    period, price = min(prices, key=lambda item: item[0])
    return price, period, currency


def _parse_finish_date(raw: object) -> datetime | None:
    if not isinstance(raw, dict):
        return None
    date_text = str(raw.get("date") or "").strip()
    if date_text:
        for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y"):
            try:
                return datetime.strptime(date_text[: len(fmt) + 3], fmt)
            except ValueError:
                continue
    epoch = raw.get("epoch")
    if isinstance(epoch, (int, float)) and epoch > 0:
        try:
            return datetime.fromtimestamp(float(epoch))
        except (OSError, OverflowError, ValueError):
            return None
    return None


def _location_label(location: str) -> str:
    key = _normalize_location(location)
    return LOCATION_LABELS.get(key, location.upper() if location else "")


def _location_code(location: str) -> str:
    key = _normalize_location(location)
    return LOCATION_CODES.get(key, key.upper() if key else "")


def _build_service_name(tariff_name: str, location: str, ip_address: str = "", os_name: str = "") -> str:
    label = _location_label(location)
    title = f"{tariff_name} ({label})" if label else tariff_name
    if ip_address:
        return f"{title} — {ip_address}"
    if os_name:
        return f"{title} — {os_name}"
    return title


def _services_from_order(order: dict[str, object], tariffs: dict[int, dict[str, object]]) -> list[RemoteService]:
    order_id = str(order.get("order_id") or "").strip()
    tariff = order.get("tariff") if isinstance(order.get("tariff"), dict) else {}
    tariff_name = str(tariff.get("name") or "OneDash").strip()
    try:
        tariff_id = int(tariff.get("id") or 0)
    except (TypeError, ValueError):
        tariff_id = 0
    location = str(order.get("location") or "").strip().lower()
    location_code = _location_code(location)
    finish_time = _parse_finish_date(order.get("finish_time"))
    next_payment_date = finish_time.date() if finish_time else None
    amount, billing_period_days, currency = _renewal_amount(order, tariffs, tariff_id, location)

    vps_list = order.get("vps_list")
    if not isinstance(vps_list, list) or not vps_list:
        if order_id:
            return [
                RemoteService(
                    service_id=order_id,
                    name=_build_service_name(tariff_name, location),
                    status="active",
                    next_payment_date=next_payment_date,
                    amount=amount,
                    currency=currency,
                    billing_period_days=billing_period_days,
                    payment_url=CABINET_URL,
                    location=location_code,
                )
            ]
        return []

    services: list[RemoteService] = []
    for vps in vps_list:
        if not isinstance(vps, dict):
            continue
        vps_id = str(vps.get("id") or "").strip()
        if not vps_id:
            continue
        ip_address = str(vps.get("vps_ip") or "").strip()
        os_name = str(vps.get("os") or "").strip()
        status_raw = str(vps.get("vps_status") or "").strip().lower()
        services.append(
            RemoteService(
                service_id=f"{order_id}:{vps_id}" if order_id else vps_id,
                name=_build_service_name(tariff_name, location, ip_address, os_name),
                ip_address=ip_address,
                status=VPS_STATUS_MAP.get(status_raw, "active"),
                next_payment_date=next_payment_date,
                amount=amount,
                currency=currency,
                billing_period_days=billing_period_days,
                payment_url=CABINET_URL,
                location=location_code,
            )
        )
    return services
