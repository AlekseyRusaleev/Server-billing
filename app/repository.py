from __future__ import annotations

from datetime import date, timedelta
from collections import defaultdict

from app.config import settings
from app.crypto import decrypt_secret, encrypt_secret, is_encrypted
from app.currency import fetch_currency_rates, rates_from_string, rates_to_string, today_label
from app.db import connect
from app.models import (
    HostingAccount,
    PaymentHistoryItem,
    Server,
    account_from_row,
    payment_history_from_row,
    server_from_row,
)


SERVER_SELECT = """
SELECT
    servers.*,
    hosting_accounts.name AS account_name,
    hosting_accounts.login AS account_login,
    hosting_accounts.auth_secret AS account_secret,
    hosting_accounts.panel_url AS account_panel_url,
    hosting_accounts.payment_url AS account_payment_url
FROM servers
LEFT JOIN hosting_accounts ON hosting_accounts.id = servers.hosting_account_id
"""


def list_accounts() -> list[HostingAccount]:
    with connect() as connection:
        rows = connection.execute(
            "SELECT * FROM hosting_accounts ORDER BY provider ASC, name ASC"
        ).fetchall()
    return [account_from_row(row) for row in rows]


def get_account(account_id: int) -> HostingAccount | None:
    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM hosting_accounts WHERE id = ?", (account_id,)
        ).fetchone()
    return account_from_row(row) if row else None


def create_account(data: dict[str, object]) -> int:
    with connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO hosting_accounts (
                name, provider, login, auth_secret, panel_url, payment_url, notes,
                integration_type, integration_url, auto_sync_enabled
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["name"],
                data["provider"],
                data.get("login", ""),
                encrypt_secret(str(data.get("auth_secret", ""))),
                data.get("panel_url", ""),
                data.get("payment_url", ""),
                data.get("notes", ""),
                data.get("integration_type", "manual") or "manual",
                data.get("integration_url", ""),
                1 if data.get("auto_sync_enabled") else 0,
            ),
        )
        return int(cursor.lastrowid)


def update_account(account_id: int, data: dict[str, object]) -> None:
    with connect() as connection:
        connection.execute(
            """
            UPDATE hosting_accounts
            SET name = ?, provider = ?, login = ?, auth_secret = ?,
                panel_url = ?, payment_url = ?, notes = ?,
                integration_type = ?, integration_url = ?, auto_sync_enabled = ?
            WHERE id = ?
            """,
            (
                data["name"],
                data["provider"],
                data.get("login", ""),
                encrypt_secret(str(data.get("auth_secret", ""))),
                data.get("panel_url", ""),
                data.get("payment_url", ""),
                data.get("notes", ""),
                data.get("integration_type", "manual") or "manual",
                data.get("integration_url", ""),
                1 if data.get("auto_sync_enabled") else 0,
                account_id,
            ),
        )


def delete_account(account_id: int) -> None:
    with connect() as connection:
        connection.execute(
            "UPDATE servers SET hosting_account_id = NULL WHERE hosting_account_id = ?",
            (account_id,),
        )
        connection.execute("DELETE FROM hosting_accounts WHERE id = ?", (account_id,))


def list_servers(
    search: str = "",
    provider: str = "",
    payment_state: str = "",
) -> list[Server]:
    with connect() as connection:
        rows = connection.execute(
            f"{SERVER_SELECT} ORDER BY servers.next_payment_date ASC, servers.provider ASC, servers.name ASC"
        ).fetchall()
    servers = [server_from_row(row) for row in rows]
    search = search.strip().lower()
    provider = provider.strip()
    payment_state = payment_state.strip()
    if search:
        servers = [
            server
            for server in servers
            if search
            in " ".join(
                [
                    server.name,
                    server.provider,
                    server.ip_address,
                    server.location,
                    server.server_login,
                    server.service_id,
                    server.account_name,
                    server.account_login,
                ]
            ).lower()
        ]
    if provider:
        servers = [server for server in servers if server.provider == provider]
    if payment_state:
        servers = [server for server in servers if server.payment_state == payment_state]
    return servers


def get_server(server_id: int) -> Server | None:
    with connect() as connection:
        row = connection.execute(
            f"{SERVER_SELECT} WHERE servers.id = ?", (server_id,)
        ).fetchone()
    return server_from_row(row) if row else None


def create_server(data: dict[str, object]) -> int:
    with connect() as connection:
        cursor = connection.execute(
            """
            INSERT INTO servers (
                hosting_account_id, name, provider, ip_address, location, server_login,
                server_password, service_id, amount, currency, billing_period_days,
                next_payment_date, payment_url, panel_url, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data.get("hosting_account_id"),
                data["name"],
                data["provider"],
                data.get("ip_address", ""),
                data.get("location", ""),
                data.get("server_login", ""),
                encrypt_secret(str(data.get("server_password", ""))),
                data.get("service_id", ""),
                data["amount"],
                data["currency"],
                data["billing_period_days"],
                data["next_payment_date"],
                data.get("payment_url", ""),
                data.get("panel_url", ""),
                data.get("notes", ""),
            ),
        )
        return int(cursor.lastrowid)


def update_server(server_id: int, data: dict[str, object]) -> None:
    with connect() as connection:
        connection.execute(
            """
            UPDATE servers
            SET hosting_account_id = ?, name = ?, provider = ?, ip_address = ?, location = ?,
                server_login = ?, server_password = ?, service_id = ?, amount = ?, currency = ?,
                billing_period_days = ?, next_payment_date = ?, payment_url = ?, panel_url = ?, notes = ?
            WHERE id = ?
            """,
            (
                data.get("hosting_account_id"),
                data["name"],
                data["provider"],
                data.get("ip_address", ""),
                data.get("location", ""),
                data.get("server_login", ""),
                encrypt_secret(str(data.get("server_password", ""))),
                data.get("service_id", ""),
                data["amount"],
                data["currency"],
                data["billing_period_days"],
                data["next_payment_date"],
                data.get("payment_url", ""),
                data.get("panel_url", ""),
                data.get("notes", ""),
                server_id,
            ),
        )


def delete_server(server_id: int) -> None:
    with connect() as connection:
        connection.execute("DELETE FROM servers WHERE id = ?", (server_id,))


def mark_paid(server_id: int, note: str = "") -> None:
    server = get_server(server_id)
    if server is None:
        return
    next_date = max(server.next_payment_date, date.today()) + timedelta(days=server.billing_period_days)
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO payment_history (
                server_id, server_name, provider, amount, currency, paid_at,
                previous_next_payment_date, next_payment_date, note
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                server.id,
                server.name,
                server.provider,
                server.amount,
                server.currency,
                date.today().isoformat(),
                server.next_payment_date.isoformat(),
                next_date.isoformat(),
                note.strip(),
            ),
        )
        connection.execute(
            """
            UPDATE servers
            SET next_payment_date = ?, last_paid_at = ?, status = 'active'
            WHERE id = ?
            """,
            (next_date.isoformat(), date.today().isoformat(), server_id),
        )


def list_payment_history(server_id: int | None = None) -> list[PaymentHistoryItem]:
    query = "SELECT * FROM payment_history"
    params: tuple[object, ...] = ()
    if server_id is not None:
        query += " WHERE server_id = ?"
        params = (server_id,)
    query += " ORDER BY paid_at DESC, created_at DESC"
    with connect() as connection:
        rows = connection.execute(query, params).fetchall()
    return [payment_history_from_row(row) for row in rows]


def monthly_expense_summary() -> list[dict[str, object]]:
    summary: dict[tuple[str, str], float] = defaultdict(float)
    for item in list_payment_history():
        month = item.paid_at.strftime("%Y-%m")
        summary[(month, item.currency)] += item.amount
    rows = [
        {"month": month, "currency": currency, "amount": amount}
        for (month, currency), amount in summary.items()
    ]
    return sorted(rows, key=lambda row: str(row["month"]), reverse=True)


def provider_expense_summary() -> list[dict[str, object]]:
    summary: dict[tuple[str, str], float] = defaultdict(float)
    for item in list_payment_history():
        summary[(item.provider, item.currency)] += item.amount
    rows = [
        {"provider": provider, "currency": currency, "amount": amount}
        for (provider, currency), amount in summary.items()
    ]
    return sorted(rows, key=lambda row: float(row["amount"]), reverse=True)


def currency_settings() -> dict[str, object]:
    rates_text = get_effective_setting("currency_rates", settings.currency_rates)
    rates = rates_from_string(rates_text)
    if "USDT" not in rates and "USD" in rates:
        rates["USDT"] = rates["USD"]
    return {
        "base": get_effective_setting("currency_base", settings.currency_base).upper(),
        "rates": rates,
        "rates_text": rates_text,
        "updated_at": get_effective_setting(
            "currency_rates_updated_at", settings.currency_rates_updated_at
        ),
    }


def refresh_currency_rates() -> dict[str, object]:
    rates = fetch_currency_rates()
    set_app_setting("currency_rates", rates_to_string(rates))
    set_app_setting("currency_rates_updated_at", today_label())
    if not get_app_setting("currency_base", ""):
        set_app_setting("currency_base", "RUB")
    return currency_settings()


def save_currency_settings(base: str, rates_text: str) -> None:
    set_app_setting("currency_base", base.strip().upper() or "RUB")
    set_app_setting("currency_rates", rates_text.strip() or "RUB:1")
    set_app_setting("currency_rates_updated_at", today_label())


def monthly_plan_summary(servers: list[Server] | None = None) -> dict[str, object]:
    servers = servers if servers is not None else list_servers()
    by_currency: dict[str, float] = defaultdict(float)
    for server in servers:
        if server.billing_period_days <= 0:
            continue
        by_currency[server.currency] += server.amount / server.billing_period_days * 30

    current_currency = currency_settings()
    base = str(current_currency["base"])
    rates = current_currency["rates"]
    total_base = 0.0
    missing: list[str] = []
    for currency, amount in by_currency.items():
        if currency == base:
            total_base += amount
        elif currency == "RUB" and base in rates and rates[base]:
            total_base += amount / rates[base]
        elif currency in rates and base == "RUB":
            total_base += amount * rates[currency]
        elif currency in rates and base in rates and rates[base]:
            total_base += amount * rates[currency] / rates[base]
        else:
            missing.append(currency)

    return {
        "by_currency": dict(sorted(by_currency.items())),
        "base": base,
        "total_base": total_base,
        "missing": sorted(set(missing)),
        "rates_updated_at": current_currency["updated_at"],
    }


SECRET_SETTING_KEYS = {"telegram_bot_token"}


def set_app_setting(key: str, value: str) -> None:
    stored_value = encrypt_secret(value) if key in SECRET_SETTING_KEYS else value
    with connect() as connection:
        connection.execute(
            """
            INSERT INTO app_settings (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, stored_value),
        )


def get_app_setting(key: str, default: str = "") -> str:
    with connect() as connection:
        row = connection.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    value = row["value"] or ""
    return decrypt_secret(value) if key in SECRET_SETTING_KEYS else value


def get_effective_setting(key: str, env_value: str = "") -> str:
    value = get_app_setting(key, "")
    return value if value != "" else env_value


def notification_settings() -> dict[str, str]:
    return {
        "telegram_bot_token": get_effective_setting(
            "telegram_bot_token", settings.telegram_bot_token
        ),
        "telegram_chat_id": get_effective_setting("telegram_chat_id", settings.telegram_chat_id),
        "reminder_days": get_effective_setting("reminder_days", settings.reminder_days),
        "check_interval_seconds": get_effective_setting(
            "check_interval_seconds", str(settings.check_interval_seconds)
        ),
        "base_url": get_effective_setting("base_url", settings.base_url),
        "backup_interval_days": get_effective_setting(
            "backup_interval_days", str(settings.backup_interval_days)
        ),
        "currency_base": get_effective_setting("currency_base", settings.currency_base),
        "currency_rates": get_effective_setting("currency_rates", settings.currency_rates),
        "currency_rates_updated_at": get_effective_setting(
            "currency_rates_updated_at", settings.currency_rates_updated_at
        ),
        "telegram_bot_username": get_effective_setting("telegram_bot_username", ""),
        "telegram_chat_title": get_effective_setting("telegram_chat_title", ""),
        "telegram_tested_at": get_effective_setting("telegram_tested_at", ""),
    }


def save_notification_settings(
    telegram_bot_token: str | None,
    telegram_chat_id: str | None,
    reminder_days: str,
    check_interval_seconds: int,
    base_url: str,
    backup_interval_days: int,
) -> None:
    if telegram_bot_token is not None and telegram_bot_token.strip():
        set_app_setting("telegram_bot_token", telegram_bot_token.strip())
    if telegram_chat_id is not None:
        set_app_setting("telegram_chat_id", telegram_chat_id.strip())
    set_app_setting("reminder_days", reminder_days.strip() or "7,3,1,0,-1")
    set_app_setting("check_interval_seconds", str(check_interval_seconds))
    set_app_setting("base_url", base_url.strip() or settings.base_url)
    set_app_setting("backup_interval_days", str(backup_interval_days))


def get_last_backup_at() -> date | None:
    value = get_app_setting("last_backup_at", "")
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def mark_backup_sent() -> None:
    set_app_setting("last_backup_at", date.today().isoformat())


def encrypt_existing_secrets() -> None:
    with connect() as connection:
        rows = connection.execute("SELECT id, auth_secret FROM hosting_accounts").fetchall()
        for row in rows:
            value = row["auth_secret"] or ""
            if value and not is_encrypted(value):
                connection.execute(
                    "UPDATE hosting_accounts SET auth_secret = ? WHERE id = ?",
                    (encrypt_secret(value), row["id"]),
                )
        rows = connection.execute("SELECT id, server_password FROM servers").fetchall()
        for row in rows:
            value = row["server_password"] or ""
            if value and not is_encrypted(value):
                connection.execute(
                    "UPDATE servers SET server_password = ? WHERE id = ?",
                    (encrypt_secret(value), row["id"]),
                )
        rows = connection.execute(
            "SELECT key, value FROM app_settings WHERE key IN ('telegram_bot_token')"
        ).fetchall()
        for row in rows:
            value = row["value"] or ""
            if value and not is_encrypted(value):
                connection.execute(
                    "UPDATE app_settings SET value = ? WHERE key = ?",
                    (encrypt_secret(value), row["key"]),
                )


def seed_demo_data() -> None:
    with connect() as connection:
        server_count = connection.execute("SELECT COUNT(*) FROM servers").fetchone()[0]
        account_count = connection.execute("SELECT COUNT(*) FROM hosting_accounts").fetchone()[0]

    demo_accounts = [
        {
            "name": "Демо · основной хостинг",
            "provider": "example-host.test",
            "login": "admin@example.com",
            "auth_secret": "demo-password",
            "panel_url": "https://example.com/",
            "payment_url": "https://example.com/",
            "notes": "Пример для ознакомления — удалите после настройки своих аккаунтов.",
        },
        {
            "name": "Демо · зарубежный VPS",
            "provider": "demo-vps.test",
            "login": "billing@example.com",
            "auth_secret": "demo-password",
            "panel_url": "https://example.com/",
            "payment_url": "https://example.com/",
            "notes": "Пример для ознакомления — удалите после настройки своих аккаунтов.",
        },
        {
            "name": "Демо · веб-хостинг",
            "provider": "demo-web.test",
            "login": "host@example.com",
            "auth_secret": "demo-password",
            "panel_url": "https://example.com/",
            "payment_url": "https://example.com/",
            "notes": "Пример для ознакомления — удалите после настройки своих аккаунтов.",
        },
    ]

    if account_count == 0:
        for account in demo_accounts:
            create_account(account)

    accounts_by_provider = {account.provider: account.id for account in list_accounts()}

    with connect() as connection:
        for provider, account_id in accounts_by_provider.items():
            connection.execute(
                """
                UPDATE servers
                SET hosting_account_id = ?
                WHERE hosting_account_id IS NULL AND provider = ?
                """,
                (account_id, provider),
            )

    if server_count:
        return

    today = date.today()
    samples = [
        {
            "name": "Демо · RDP сервер",
            "hosting_account_id": accounts_by_provider.get("example-host.test"),
            "provider": "example-host.test",
            "ip_address": "185.10.10.21",
            "location": "Россия",
            "server_login": "root",
            "server_password": "demo-password",
            "service_id": "vds-1021",
            "amount": 950,
            "currency": "RUB",
            "billing_period_days": 30,
            "next_payment_date": (today + timedelta(days=2)).isoformat(),
            "payment_url": "https://example.com/",
            "panel_url": "https://example.com/",
            "notes": "Пример сервера — удалите после добавления своих.",
        },
        {
            "name": "Демо · Proxy Node",
            "hosting_account_id": accounts_by_provider.get("demo-vps.test"),
            "provider": "demo-vps.test",
            "ip_address": "91.200.14.8",
            "location": "Нидерланды",
            "server_login": "root",
            "server_password": "demo-password",
            "service_id": "q-7781",
            "amount": 12,
            "currency": "USD",
            "billing_period_days": 30,
            "next_payment_date": (today + timedelta(days=6)).isoformat(),
            "payment_url": "https://example.com/",
            "panel_url": "https://example.com/",
            "notes": "Пример сервера — удалите после добавления своих.",
        },
        {
            "name": "Демо · веб-лендинг",
            "hosting_account_id": accounts_by_provider.get("demo-web.test"),
            "provider": "demo-web.test",
            "ip_address": "77.77.33.10",
            "location": "Россия",
            "server_login": "root",
            "server_password": "demo-password",
            "service_id": "h-428",
            "amount": 420,
            "currency": "RUB",
            "billing_period_days": 30,
            "next_payment_date": (today + timedelta(days=18)).isoformat(),
            "payment_url": "https://example.com/",
            "panel_url": "https://example.com/",
            "notes": "Пример сервера — удалите после добавления своих.",
        },
    ]

    for sample in samples:
        create_server(sample)
