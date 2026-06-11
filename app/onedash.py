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


def _monthly_amount(tariffs: dict[int, dict[str, object]], tariff_id: int, location: str) -> tuple[float | None, int, str]:
    tariff = tariffs.get(tariff_id)
    if not tariff:
        return None, 30, "RUB"

    location_key = f"{location.strip().lower()}_prices"
    if location.lower() in {"msk", "ru"}:
        location_key = "msk_prices"
    elif location.lower() in {"ams", "nl"}:
        location_key = "ams_prices"
    prices = tariff.get(location_key)
    if not isinstance(prices, list) or not prices:
        return None, 30, str(tariff.get("currency") or "RUB")

    currency = str(tariff.get("currency") or "RUB")
    normalized: list[tuple[int, float]] = []
    for row in prices:
        if not isinstance(row, dict):
            continue
        try:
            period = int(row.get("period") or 0)
        except (TypeError, ValueError):
            continue
        if period <= 0:
            continue
        normalized.append((period, _effective_price(row)))

    if not normalized:
        return None, 30, currency

    for period, price in normalized:
        if period == 30:
            return price, 30, currency

    period, price = min(normalized, key=lambda item: item[1] / item[0])
    monthly = price / period * 30
    return monthly, 30, currency


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


def _services_from_order(order: dict[str, object], tariffs: dict[int, dict[str, object]]) -> list[RemoteService]:
    order_id = str(order.get("order_id") or "").strip()
    tariff = order.get("tariff") if isinstance(order.get("tariff"), dict) else {}
    tariff_name = str(tariff.get("name") or "OneDash").strip()
    try:
        tariff_id = int(tariff.get("id") or 0)
    except (TypeError, ValueError):
        tariff_id = 0
    location = str(order.get("location") or "").strip().lower()
    finish_time = _parse_finish_date(order.get("finish_time"))
    next_payment_date = finish_time.date() if finish_time else None
    amount, billing_period_days, currency = _monthly_amount(tariffs, tariff_id, location)

    vps_list = order.get("vps_list")
    if not isinstance(vps_list, list) or not vps_list:
        if order_id:
            return [
                RemoteService(
                    service_id=order_id,
                    name=f"{tariff_name} {location.upper()}".strip(),
                    status="active",
                    next_payment_date=next_payment_date,
                    amount=amount,
                    currency=currency,
                    billing_period_days=billing_period_days,
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
        name_parts = [tariff_name]
        if location:
            name_parts.append(location.upper())
        if os_name:
            name_parts.append(os_name)
        if ip_address:
            name_parts.append(ip_address)
        services.append(
            RemoteService(
                service_id=f"{order_id}:{vps_id}" if order_id else vps_id,
                name=" · ".join(name_parts),
                ip_address=ip_address,
                status=VPS_STATUS_MAP.get(status_raw, "active"),
                next_payment_date=next_payment_date,
                amount=amount,
                currency=currency,
                billing_period_days=billing_period_days,
            )
        )
    return services
