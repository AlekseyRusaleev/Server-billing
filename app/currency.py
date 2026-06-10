from __future__ import annotations

import urllib.request
import xml.etree.ElementTree as ET
from datetime import date
import json

CBR_DAILY_URL = "https://www.cbr.ru/scripts/XML_daily.asp"
COINGECKO_USDT_RUB_URL = (
    "https://api.coingecko.com/api/v3/simple/price?ids=tether&vs_currencies=rub"
)


def parse_rates(raw: str) -> dict[str, float]:
    rates = {"RUB": 1.0}
    root = ET.fromstring(raw)
    for item in root.findall("Valute"):
        code = item.findtext("CharCode", "").strip().upper()
        nominal = float(item.findtext("Nominal", "1").replace(",", "."))
        value = float(item.findtext("Value", "0").replace(",", "."))
        if code and nominal:
            rates[code] = value / nominal
    return rates


def fetch_cbr_rates() -> dict[str, float]:
    with urllib.request.urlopen(CBR_DAILY_URL, timeout=20) as response:
        raw = response.read().decode("windows-1251")
    return parse_rates(raw)


def fetch_usdt_rub_rate() -> float | None:
    request = urllib.request.Request(
        COINGECKO_USDT_RUB_URL,
        headers={"User-Agent": "server-billing-manager/1.0"},
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        data = json.loads(response.read().decode("utf-8"))
    value = data.get("tether", {}).get("rub")
    return float(value) if value else None


def fetch_currency_rates() -> dict[str, float]:
    rates = fetch_cbr_rates()
    try:
        usdt = fetch_usdt_rub_rate()
    except Exception:
        usdt = None
    if usdt:
        rates["USDT"] = usdt
    elif "USD" in rates:
        rates["USDT"] = rates["USD"]
    return rates


def rates_to_string(rates: dict[str, float]) -> str:
    return ",".join(f"{code}:{value:.8f}" for code, value in sorted(rates.items()))


def rates_from_string(value: str) -> dict[str, float]:
    rates = {"RUB": 1.0}
    for part in value.split(","):
        if ":" not in part:
            continue
        code, rate = part.split(":", 1)
        try:
            rates[code.strip().upper()] = float(rate)
        except ValueError:
            continue
    return rates


def today_label() -> str:
    return date.today().isoformat()
