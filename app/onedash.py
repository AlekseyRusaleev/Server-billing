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
LOCATION_TARIFF_KEYS = {
    "msk": ("new_prices", "msk_prices"),
    "ams": ("new_prices_ams", "ams_prices"),
    "hel": ("new_prices_hel", "hel_prices"),
    "fra": ("new_prices_fra", "fra_prices"),
    "nyc": ("new_prices_nyc", "nyc_prices"),
    "lon": ("new_prices_lon", "lon_prices"),
}
STANDARD_PERIODS = (7, 10, 14, 30, 60, 90, 180, 360, 720, 999)
DEFAULT_RENT_PERIOD = 30
_PERIOD_SKIP_KEYS = frozenset(
    {
        "days_remaining",
        "days_left",
        "days_until",
        "days_until_expiry",
        "days_to_finish",
    }
)

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

    def list_services(self, *, known_periods: dict[str, int] | None = None) -> list[RemoteService]:
        tariffs = _load_tariffs(self._request("tariffs"))
        payload = self._request("all-orders")
        orders = payload.get("data")
        if not isinstance(orders, list):
            return []

        period_hints = known_periods or {}
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
            services.extend(_services_from_order(order, tariffs, period_hints))
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


def _decode_tariff_prices(raw: object, currency: str = "RUB") -> list[dict[str, object]]:
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            return []
    return _price_bucket(raw, currency)


def _location_prices(tariff: dict[str, object], location: str) -> list[dict[str, object]]:
    loc = _normalize_location(location)
    currency = str(tariff.get("currency") or "RUB")
    candidate_keys = list(LOCATION_TARIFF_KEYS.get(loc, (f"new_prices_{loc}", f"{loc}_prices")))
    for key in candidate_keys:
        if key not in tariff:
            continue
        bucket = _decode_tariff_prices(tariff.get(key), currency)
        if bucket:
            return bucket

    for key, value in tariff.items():
        if not isinstance(key, str):
            continue
        if key in candidate_keys:
            continue
        if loc not in key.lower():
            continue
        if not (key.endswith("_prices") or key.startswith("new_prices")):
            continue
        bucket = _decode_tariff_prices(value, currency)
        if bucket:
            return bucket
    return []


def _snap_period(days: int) -> int | None:
    if days in STANDARD_PERIODS:
        return days
    for period in STANDARD_PERIODS:
        tolerance = max(2, period // 30)
        if abs(days - period) <= tolerance:
            return period
    return None


def _epoch_from(raw: object) -> float | None:
    if isinstance(raw, dict):
        epoch = raw.get("epoch")
        if isinstance(epoch, (int, float)) and epoch > 0:
            return float(epoch)
    if isinstance(raw, (int, float)) and raw > 0:
        return float(raw)
    return None


def _period_from_timestamps(order: dict[str, object]) -> int | None:
    finish_epoch = _epoch_from(order.get("finish_time"))
    start_epoch = None
    for key in (
        "start_time",
        "create_time",
        "created_at",
        "begin_time",
        "order_time",
        "paid_at",
        "start_at",
        "opened_at",
    ):
        if start_epoch is None:
            start_epoch = _epoch_from(order.get(key))
    if finish_epoch is None or start_epoch is None or finish_epoch <= start_epoch:
        return None
    return _snap_period(int(round((finish_epoch - start_epoch) / 86400)))


def _scan_order_period(raw: object, *, depth: int = 0) -> int | None:
    if depth > 5:
        return None
    if isinstance(raw, dict):
        for key, value in raw.items():
            key_l = str(key).lower()
            if key_l in _PERIOD_SKIP_KEYS:
                continue
            if key_l in {"period", "now_days", "nowdays", "order_days", "rent_days", "pay_days", "rental_period"}:
                period = _parse_period(value)
                if period is not None:
                    return period
            if key_l.endswith("_days") and key_l not in _PERIOD_SKIP_KEYS:
                period = _parse_period(value)
                if period is not None:
                    return period
        for value in raw.values():
            period = _scan_order_period(value, depth=depth + 1)
            if period is not None:
                return period
    elif isinstance(raw, list):
        for item in raw:
            period = _scan_order_period(item, depth=depth + 1)
            if period is not None:
                return period
    return None


def _order_period(order: dict[str, object]) -> int | None:
    for key in (
        "period",
        "now_days",
        "nowDays",
        "order_days",
        "rent_period",
        "billing_period",
        "renew_period",
        "pay_period",
        "rental_period",
    ):
        period = _parse_period(order.get(key))
        if period is not None:
            return period
    for nested_key in ("payment", "renew", "billing", "price_info"):
        nested = order.get(nested_key)
        if isinstance(nested, dict):
            for key in ("period", "rent_period", "billing_period", "renew_period", "now_days", "nowDays"):
                period = _parse_period(nested.get(key))
                if period is not None:
                    return period
    finish = order.get("finish_time")
    if isinstance(finish, dict):
        for key in ("period", "rent_period", "billing_period", "order_days"):
            period = _parse_period(finish.get(key))
            if period is not None:
                return period
    scanned = _scan_order_period(order)
    if scanned is not None:
        return scanned
    return _period_from_timestamps(order)


def _resolve_order_period(order: dict[str, object], fallback_period: int | None = None) -> int:
    period = _order_period(order)
    if period is not None:
        return period
    if fallback_period is not None and fallback_period >= 7:
        return fallback_period
    return DEFAULT_RENT_PERIOD


def _fallback_period_for_order(
    order_id: str,
    vps_id: str,
    known_periods: dict[str, int],
) -> int | None:
    if order_id and vps_id:
        service_id = f"{order_id}:{vps_id}"
        if service_id in known_periods:
            return known_periods[service_id]
    if order_id and order_id in known_periods:
        return known_periods[order_id]
    return None


def _parse_period(raw: object) -> int | None:
    if raw is None:
        return None
    try:
        period = int(raw)
    except (TypeError, ValueError):
        return None
    return period if 7 <= period <= 999 else None


def _localized_amount(raw: object, currency: str = "RUB") -> float:
    if isinstance(raw, dict):
        for key in (currency.lower(), "ru", "rub", "en", "eu"):
            amount = _parse_amount(raw.get(key))
            if amount is not None:
                return amount
        return 0.0
    if isinstance(raw, str):
        text = raw.strip()
        if text.startswith("{"):
            try:
                return _localized_amount(json.loads(text), currency)
            except json.JSONDecodeError:
                pass
        return _parse_amount(raw) or 0.0
    return _parse_amount(raw) or 0.0


def _addon_multiplier(period: int) -> float:
    if period == 999:
        return 1.0
    if period > 30:
        return period / 30
    return 1.0


def _extra_charges(order: dict[str, object], period: int, currency: str) -> float:
    extra = _localized_amount(order.get("dop_amount"), currency)
    if extra <= 0:
        return 0.0
    return extra * _addon_multiplier(period)


def _price_for_period(prices: list[tuple[int, float]], period: int) -> float | None:
    for row_period, price in prices:
        if row_period == period:
            return price
    return None


def _parse_amount(raw: object) -> float | None:
    if raw is None:
        return None
    try:
        amount = float(raw)
    except (TypeError, ValueError):
        return None
    return amount if amount > 0 else None


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
    *,
    fallback_period: int | None = None,
) -> tuple[float | None, int, str]:
    period = _resolve_order_period(order, fallback_period)
    direct_amount, direct_period, direct_currency = _order_amount_from_payload(order)
    if direct_amount is not None:
        return direct_amount, direct_period or period, direct_currency

    tariff = tariffs.get(tariff_id)
    if not tariff:
        return None, period, "RUB"

    currency = str(tariff.get("currency") or "RUB")
    prices = _price_rows(_location_prices(tariff, location))
    if not prices:
        logger.warning(
            "OneDash: нет цен для тарифа %s в локации %s (order %s).",
            tariff_id,
            location,
            order.get("order_id"),
        )
        return None, period, currency

    base = _price_for_period(prices, period)
    if base is None:
        logger.warning(
            "OneDash: нет цены для периода %s (тариф %s, локация %s, order %s).",
            period,
            tariff_id,
            location,
            order.get("order_id"),
        )
        return None, period, currency

    order_count = max(1, int(order.get("order_count") or 1))
    total = (base + _extra_charges(order, period, currency)) * order_count
    return total, period, currency


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


def _services_from_order(
    order: dict[str, object],
    tariffs: dict[int, dict[str, object]],
    known_periods: dict[str, int] | None = None,
) -> list[RemoteService]:
    period_hints = known_periods or {}
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
    order_fallback = _fallback_period_for_order(order_id, "", period_hints)

    vps_list = order.get("vps_list")
    if not isinstance(vps_list, list) or not vps_list:
        if order_id:
            amount, billing_period_days, currency = _renewal_amount(
                order,
                tariffs,
                tariff_id,
                location,
                fallback_period=order_fallback,
            )
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
        fallback_period = _fallback_period_for_order(order_id, vps_id, period_hints) or order_fallback
        amount, billing_period_days, currency = _renewal_amount(
            order,
            tariffs,
            tariff_id,
            location,
            fallback_period=fallback_period,
        )
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
