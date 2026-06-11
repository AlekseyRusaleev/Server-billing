from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path


TEMPLATES_PATH = Path(__file__).with_name("provider_templates.json")

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
}

PRICE_HINT_BY_DOMAIN = {
    "hetzner.com": "тариф на сайте",
    "digitalocean.com": "от $4/мес",
    "vultr.com": "от $2.50/мес",
    "linode.com": "от $5/мес",
    "ovhcloud.com": "тариф на сайте",
    "scaleway.com": "тариф на сайте",
    "contabo.com": "тариф на сайте",
    "timeweb.cloud": "тариф на сайте",
    "selectel.ru": "тариф на сайте",
    "reg.ru": "тариф на сайте",
    "beget.com": "тариф на сайте",
    "firstvds.ru": "тариф на сайте",
    "vdsina.ru": "тариф на сайте",
    "aeza.net": "тариф на сайте",
    "fornex.com": "тариф на сайте",
    "onlinevds.ru": "от ~500 ₽/мес",
    "hostoff.net": "тариф на сайте",
    "rdp-onedash.ru": "тариф на сайте",
    "ruvds.com": "от ~200 ₽/мес",
    "adminvps.ru": "тариф на сайте",
    "zomro.com": "тариф на сайте",
    "serverspace.ru": "тариф на сайте",
    "mchost.ru": "тариф на сайте",
    "sprinthost.ru": "тариф на сайте",
    "eurohoster.org": "тариф на сайте",
    "ispserver.com": "тариф на сайте",
    "ionos.com": "от €4/мес",
    "hostinger.com": "от $4/мес",
    "aws.amazon.com": "от $3.50/мес",
    "cloud.google.com": "по факту использования",
    "kamatera.com": "от $4/мес",
    "upcloud.com": "от $5/мес",
    "cherryservers.com": "тариф на сайте",
    "leaseweb.com": "тариф на сайте",
}


@lru_cache(maxsize=1)
def list_provider_templates() -> list[dict[str, object]]:
    with TEMPLATES_PATH.open(encoding="utf-8-sig") as file:
        providers = json.load(file)
    for provider in providers:
        domain = str(provider.get("domain", ""))
        countries = COUNTRIES_BY_DOMAIN.get(domain, [])
        provider["countries"] = countries
        provider["country_labels"] = [COUNTRY_LABELS[code] for code in countries if code in COUNTRY_LABELS]
        provider["price_hint"] = PRICE_HINT_BY_DOMAIN.get(domain, "тариф на сайте")
    return sorted(providers, key=lambda item: str(item["name"]).lower())


def provider_countries(providers: list[dict[str, object]] | None = None) -> list[dict[str, str]]:
    providers = providers or list_provider_templates()
    codes = sorted({code for provider in providers for code in provider.get("countries", [])})
    return [
        {"code": code, "name": COUNTRY_LABELS[code]["name"], "flag": COUNTRY_LABELS[code]["flag"]}
        for code in codes
        if code in COUNTRY_LABELS
    ]
