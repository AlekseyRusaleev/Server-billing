from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from app.config import settings


APP_DIR = Path(__file__).resolve().parent
TEMPLATES_PATH = APP_DIR / "provider_templates.json"
CATALOG_PATH = APP_DIR / "provider_catalog.json"
PLANS_PATH = APP_DIR / "provider_plans.json"
CACHE_DIR = Path(settings.database_path).resolve().parent / "catalog_cache"
BUNDLE_CACHE = CACHE_DIR / "provider_bundle.json"

COUNTRY_LABELS = {
    "RU": {"name": "Россия", "flag": "🇷🇺"},
    "NL": {"name": "Нидерланды", "flag": "🇳🇱"},
    "DE": {"name": "Германия", "flag": "🇩🇪"},
    "FI": {"name": "Финляндия", "flag": "🇫🇮"},
    "FR": {"name": "Франция", "flag": "🇫🇷"},
    "PL": {"name": "Польша", "flag": "🇵🇱"},
    "GB": {"name": "Великобритания", "flag": "🇬🇧"},
    "US": {"name": "США", "flag": "🇺🇸"},
    "CA": {"name": "Канада", "flag": "🇨🇦"},
    "SG": {"name": "Сингапур", "flag": "🇸🇬"},
}

COUNTRIES_BY_DOMAIN = {
    "hetzner.com": ["DE", "FI", "US"],
    "digitalocean.com": ["US", "NL", "DE", "GB", "SG"],
    "vultr.com": ["US", "NL", "DE", "FR", "GB", "SG"],
    "linode.com": ["US", "DE", "GB", "SG"],
    "ovhcloud.com": ["FR", "DE", "PL", "GB", "CA", "US"],
    "scaleway.com": ["FR", "NL", "PL"],
    "contabo.com": ["DE", "US", "SG"],
    "timeweb.cloud": ["RU", "NL", "PL"],
    "selectel.ru": ["RU"],
    "reg.ru": ["RU"],
    "beget.com": ["RU"],
    "firstvds.ru": ["RU", "NL"],
    "vdsina.ru": ["RU"],
    "aeza.net": ["RU", "NL", "DE", "US"],
    "fornex.com": ["NL", "DE", "US"],
    "onlinevds.ru": ["RU"],
    "hostoff.net": ["RU", "NL"],
    "rdp-onedash.ru": ["RU"],
    "ruvds.com": ["RU", "NL"],
    "adminvps.ru": ["RU"],
    "zomro.com": ["NL", "DE", "PL"],
    "serverspace.ru": ["RU"],
    "mchost.ru": ["RU"],
    "sprinthost.ru": ["RU"],
    "eurohoster.org": ["NL", "DE", "PL"],
    "ispserver.com": ["RU", "NL"],
    "ionos.com": ["DE", "US", "GB"],
    "hostinger.com": ["US", "NL", "DE", "GB", "SG"],
    "aws.amazon.com": ["US", "DE", "GB", "SG"],
    "cloud.google.com": ["US", "NL", "DE", "SG"],
    "kamatera.com": ["US", "NL", "DE", "SG"],
    "upcloud.com": ["FI", "DE", "US", "SG"],
    "cherryservers.com": ["NL", "DE", "US"],
    "leaseweb.com": ["NL", "DE", "US", "SG"],
    "4vps.su": ["RU", "NL", "DE", "US", "FI", "FR"],
}

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


def _plan_price_value(plan: dict[str, object]) -> float | None:
    price = plan.get("price")
    if price is None:
        return None
    try:
        return float(price)
    except (TypeError, ValueError):
        return None


def _format_price_hint(plan: dict[str, object]) -> str:
    if plan.get("price_label"):
        return str(plan["price_label"])
    price = _plan_price_value(plan)
    currency = str(plan.get("currency") or "")
    if price is None:
        return "тариф на сайте"
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
    currencies = sorted({str(plan.get("currency")) for plan in plans if plan.get("currency")})
    return {
        "min_price": _plan_price_value(cheapest) if cheapest else None,
        "min_price_currency": str(cheapest.get("currency") or ""),
        "min_price_label": _format_price_hint(cheapest) if cheapest else "тариф на сайте",
        "min_ram_gb": min(ram_values) if ram_values else 0,
        "max_ram_gb": max(ram_values) if ram_values else 0,
        "min_cpu": min(cpu_values) if cpu_values else 0,
        "plan_currencies": currencies,
    }


def _enrich_provider(provider: dict[str, object], plans_by_domain: dict[str, list[dict[str, object]]]) -> dict[str, object]:
    domain = str(provider.get("domain", ""))
    countries = COUNTRIES_BY_DOMAIN.get(domain, [])
    provider["countries"] = countries
    provider["country_labels"] = [COUNTRY_LABELS[code] for code in countries if code in COUNTRY_LABELS]
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
    enriched = [_enrich_provider(provider, plans_by_domain) for provider in _load_raw_providers()]
    return sorted(enriched, key=lambda item: str(item["name"]).lower())


def provider_countries(providers: list[dict[str, object]] | None = None) -> list[dict[str, str]]:
    providers = providers or list_provider_templates()
    codes = sorted({code for provider in providers for code in provider.get("countries", [])})
    return [
        {"code": code, "name": COUNTRY_LABELS[code]["name"], "flag": COUNTRY_LABELS[code]["flag"]}
        for code in codes
        if code in COUNTRY_LABELS
    ]


@lru_cache(maxsize=1)
def provider_catalog_meta() -> dict[str, object]:
    bundle = _bundle_payload()
    if bundle:
        from app.catalog_sync import catalog_sync_status

        status = catalog_sync_status()
        return {
            "notice": str(bundle.get("notice") or ""),
            "promos": list(bundle.get("promos") or []),
            "updated_at": str(bundle.get("updated_at") or status.get("updated_at", "")),
            "source": status.get("source", "remote"),
        }
    payload = _load_json(CATALOG_PATH)
    if isinstance(payload, dict):
        return {
            "notice": str(payload.get("notice", "")),
            "promos": list(payload.get("promos") or []),
            "updated_at": "",
            "source": "bundled",
        }
    return {"notice": "", "promos": [], "updated_at": "", "source": "bundled"}


def provider_template_by_domain(domain: str) -> dict[str, object] | None:
    normalized = domain.strip().lower()
    for provider in list_provider_templates():
        if str(provider.get("domain", "")).lower() == normalized:
            return provider
    return None


def clear_provider_catalog_cache() -> None:
    list_provider_templates.cache_clear()
    provider_catalog_meta.cache_clear()
    load_plans_by_domain.cache_clear()
