from __future__ import annotations

"""Справочник типов интеграций для аккаунтов хостинга."""

INTEGRATION_OPTIONS: list[dict[str, str]] = [
    {
        "id": "manual",
        "label": "Ручной",
        "hint": "Любой хостер без API: даты и суммы вводите сами.",
    },
    {
        "id": "billmanager",
        "label": "BILLmanager",
        "hint": "URL биллинга (https://my.qwins.co/billmgr), логин и пароль от кабинета. Подтягивает VPS, даты и суммы.",
    },
    {
        "id": "onedash",
        "label": "OneDash API",
        "hint": "Api-Key из личного кабинета OneDash. Подтягивает заказы, IP и даты окончания.",
    },
]

INTEGRATION_LABELS = {item["id"]: item["label"] for item in INTEGRATION_OPTIONS}
SUPPORTED_INTEGRATIONS = set(INTEGRATION_LABELS)


def integration_hint(integration_type: str) -> str:
    for item in INTEGRATION_OPTIONS:
        if item["id"] == integration_type:
            return item["hint"]
    return INTEGRATION_OPTIONS[0]["hint"]
