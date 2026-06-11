from __future__ import annotations

import json
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from app.config import settings
from app.countries import all_country_options, country_labels, country_label


_RU_MONTHS = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)
APP_DIR = Path(__file__).resolve().parent
TEMPLATES_PATH = APP_DIR / "provider_templates.json"
CATALOG_PATH = APP_DIR / "provider_catalog.json"
PLANS_PATH = APP_DIR / "provider_plans.json"
LOCATIONS_PATH = APP_DIR / "provider_locations.json"
CACHE_DIR = Path(settings.database_path).resolve().parent / "catalog_cache"
BUNDLE_CACHE = CACHE_DIR / "provider_bundle.json"

API_DOCS_BY_DOMAIN = {
    "hetzner.com": "https://docs.hetzner.cloud/",
    "digitalocean.com": "https://docs.digitalocean.com/reference/api/",
    "vultr.com": "https://www.vultr.com/api/",
    "linode.com": "https://techdocs.akamai.com/linode-api/reference/api",
    "ovhcloud.com": "https://api.ovh.com/",
    "scaleway.com": "https://www.scaleway.com/en/developers/api/",
    "selectel.ru": "https://docs.selectel.ru/api/",
    "timeweb.cloud": "https://timeweb.cloud/api-docs",
    "aws.amazon.com": "https://docs.aws.amazon.com/lightsail/",
    "cloud.google.com": "https://cloud.google.com/compute/docs/reference/rest/v1",
    "4vps.su": "https://4vps.su/page/api",
}

CURRENCY_SYMBOL = {"RUB": "₽", "USD": "$", "EUR": "€"}


def _load_json(path: Path) -> object:
    with path.open(encoding="utf-8-sig") as file:
        return json.load(file)


def _bundle_payload() -> dict[str, object] | None:
    if not BUNDLE_CACHE.exists():
        return None
    payload = _load_json(BUNDLE_CACHE)
    return payload if isinstance(payload, dict) else None


@lru_cache(maxsize=1)
def load_countries_by_domain() -> dict[str, list[str]]:
    bundle = _bundle_payload()
    if bundle and isinstance(bundle.get("countries_by_domain"), dict):
        return {str(key): list(value) for key, value in bundle["countries_by_domain"].items()}
    payload = _load_json(LOCATIONS_PATH)
    raw = payload.get("countries_by_domain") if isinstance(payload, dict) else {}
    return {str(key): list(value) for key, value in raw.items()} if isinstance(raw, dict) else {}


@lru_cache(maxsize=1)
def load_plans_by_domain() -> dict[str, list[dict[str, object]]]:
    bundle = _bundle_payload()
    if bundle and bundle.get("plans_by_domain"):
        raw = bundle["plans_by_domain"]
        if isinstance(raw, dict):
            return {str(key): list(value) for key, value in raw.items()}
    payload = _load_json(PLANS_PATH)
    if isinstance(payload, dict) and payload.get("plans_by_domain"):
        raw = payload["plans_by_domain"]
        if isinstance(raw, dict):
            return {str(key): list(value) for key, value in raw.items()}
    return {}


def format_ru_calendar_date(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    for candidate in (raw[:10], raw):
        for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
            try:
                parsed = datetime.strptime(candidate, fmt)
                return f"{parsed.day} {_RU_MONTHS[parsed.month - 1]} {parsed.year}"
            except ValueError:
                continue
    return raw


def plans_prices_as_of() -> str:
    bundle = _bundle_payload()
    if bundle and bundle.get("prices_as_of"):
        return str(bundle["prices_as_of"])
    payload = _load_json(PLANS_PATH)
    if isinstance(payload, dict) and payload.get("prices_as_of"):
        return str(payload["prices_as_of"])
    return ""


def _plan_price_value(plan: dict[str, object]) -> float | None:
    price = plan.get("price")
    if price is None:
        return None
    try:
        return float(price)
    except (TypeError, ValueError):
        return None


def _plan_traffic_value(plan: dict[str, object]) -> float | None:
    if plan.get("traffic_unlimited"):
        return None
    traffic = plan.get("traffic_tb")
    if traffic is None:
        return None
    try:
        return float(traffic)
    except (TypeError, ValueError):
        return None


def _format_price_hint(plan: dict[str, object]) -> str:
    if plan.get("price_label"):
        return str(plan["price_label"])
    price = _plan_price_value(plan)
    currency = str(plan.get("currency") or "")
    if price is None:
        return "уточняйте на сайте"
    symbol = CURRENCY_SYMBOL.get(currency, currency)
    if currency == "RUB":
        return f"от ~{int(price)} {symbol}/мес"
    return f"от {symbol}{price:g}/мес"


def _normalize_plans(provider: dict[str, object], plans_by_domain: dict[str, list[dict[str, object]]]) -> list[dict[str, object]]:
    domain = str(provider.get("domain", ""))
    plans = provider.get("plans") or plans_by_domain.get(domain) or []
    if plans:
        return list(plans)
    return [{"name": "Старт", "price_label": "уточняйте на сайте"}]


def _summarize_plans(plans: list[dict[str, object]]) -> dict[str, object]:
    priced = [plan for plan in plans if _plan_price_value(plan) is not None]
    cheapest = min(priced, key=_plan_price_value) if priced else (plans[0] if plans else {})
    ram_values = [float(plan["ram_gb"]) for plan in plans if plan.get("ram_gb") is not None]
    cpu_values = [float(plan["cpu"]) for plan in plans if plan.get("cpu") is not None]
    traffic_values = [_plan_traffic_value(plan) for plan in plans]
    traffic_numbers = [value for value in traffic_values if value is not None]
    has_unlimited_traffic = any(plan.get("traffic_unlimited") for plan in plans)
    currencies = sorted({str(plan.get("currency")) for plan in plans if plan.get("currency")})
    max_price = max((_plan_price_value(plan) or 0) for plan in priced) if priced else None
    return {
        "min_price": _plan_price_value(cheapest) if cheapest else None,
        "max_price": max_price,
        "min_price_currency": str(cheapest.get("currency") or ""),
        "min_price_label": _format_price_hint(cheapest) if cheapest else "уточняйте на сайте",
        "min_ram_gb": min(ram_values) if ram_values else 0,
        "max_ram_gb": max(ram_values) if ram_values else 0,
        "min_cpu": min(cpu_values) if cpu_values else 0,
        "max_cpu": max(cpu_values) if cpu_values else 0,
        "min_traffic_tb": min(traffic_numbers) if traffic_numbers else 0,
        "max_traffic_tb": max(traffic_numbers) if traffic_numbers else 0,
        "has_unlimited_traffic": has_unlimited_traffic,
        "plan_currencies": currencies,
    }


def _enrich_provider(
    provider: dict[str, object],
    plans_by_domain: dict[str, list[dict[str, object]]],
    countries_by_domain: dict[str, list[str]],
) -> dict[str, object]:
    domain = str(provider.get("domain", ""))
    countries = list(provider.get("countries") or countries_by_domain.get(domain, []))
    provider["countries"] = countries
    provider["country_labels"] = country_labels(countries)
    provider["plans"] = _normalize_plans(provider, plans_by_domain)
    summary = _summarize_plans(provider["plans"])
    provider.update(summary)
    provider["price_hint"] = summary["min_price_label"]
    provider["visit_url"] = str(provider.get("referral_url") or provider.get("website_url") or "")
    provider["api_docs_url"] = str(provider.get("api_docs_url") or API_DOCS_BY_DOMAIN.get(domain, ""))
    provider["has_api"] = bool(provider["api_docs_url"])
    provider["integration_type"] = str(provider.get("integration_type") or "manual")
    provider["promo_text"] = str(provider.get("promo_text") or "")
    provider["sponsored"] = bool(provider.get("sponsored"))
    provider["featured"] = bool(provider.get("featured"))
    return provider


def _load_raw_providers() -> list[dict[str, object]]:
    bundle = _bundle_payload()
    if bundle and bundle.get("providers"):
        raw = bundle["providers"]
        if isinstance(raw, list) and raw:
            return [dict(item) for item in raw]
    payload = _load_json(TEMPLATES_PATH)
    if isinstance(payload, list):
        return [dict(item) for item in payload]
    return []


@lru_cache(maxsize=1)
def list_provider_templates() -> list[dict[str, object]]:
    plans_by_domain = load_plans_by_domain()
    countries_by_domain = load_countries_by_domain()
    enriched = [
        _enrich_provider(provider, plans_by_domain, countries_by_domain)
        for provider in _load_raw_providers()
    ]
    return sorted(enriched, key=lambda item: str(item["name"]).lower())


def provider_countries(providers: list[dict[str, object]] | None = None) -> list[dict[str, str]]:
    providers = providers or list_provider_templates()
    codes = sorted({code for provider in providers for code in provider.get("countries", [])})
    return [item for code in codes if (item := country_label(code))]


def provider_catalog_meta() -> dict[str, object]:
    bundle = _bundle_payload()
    if bundle:
        from app.catalog_sync import catalog_sync_status

        status = catalog_sync_status()
        prices_as_of = str(bundle.get("prices_as_of") or plans_prices_as_of())
        return {
            "notice": str(bundle.get("notice") or ""),
            "promos": list(bundle.get("promos") or []),
            "updated_at": str(bundle.get("updated_at") or status.get("updated_at", "")),
            "prices_as_of": prices_as_of,
            "prices_as_of_label": format_ru_calendar_date(prices_as_of),
            "source": status.get("source", "remote"),
            "sync_enabled": bool((settings.provider_catalog_url or "").strip()),
        }
    payload = _load_json(CATALOG_PATH)
    if isinstance(payload, dict):
        prices_as_of = plans_prices_as_of()
        return {
            "notice": str(payload.get("notice", "")),
            "promos": list(payload.get("promos") or []),
            "updated_at": "",
            "prices_as_of": prices_as_of,
            "prices_as_of_label": format_ru_calendar_date(prices_as_of),
            "source": "bundled",
            "sync_enabled": bool((settings.provider_catalog_url or "").strip()),
        }
    prices_as_of = plans_prices_as_of()
    return {
        "notice": "",
        "promos": [],
        "updated_at": "",
        "prices_as_of": prices_as_of,
        "prices_as_of_label": format_ru_calendar_date(prices_as_of),
        "source": "bundled",
        "sync_enabled": bool((settings.provider_catalog_url or "").strip()),
    }


def provider_template_by_domain(domain: str) -> dict[str, object] | None:
    normalized = domain.strip().lower()
    for provider in list_provider_templates():
        if str(provider.get("domain", "")).lower() == normalized:
            return provider
    return None


def clear_provider_catalog_cache() -> None:
    from app.countries import clear_country_catalog_cache

    list_provider_templates.cache_clear()
    load_plans_by_domain.cache_clear()
    load_countries_by_domain.cache_clear()
    clear_country_catalog_cache()
