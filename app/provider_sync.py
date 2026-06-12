"""Синхронизация услуг провайдера в локальную базу.

Поток: коннектор провайдера -> нормализованные услуги -> upsert серверов.
Политика конфликтов:
  - из API обновляем: next_payment_date, amount, currency, status, payment_url;
  - name — только если пусто или совпадает с service_id (ручное имя не перетираем);
  - billing_period_days — для OneDash обновляем из API (период аренды заказа);
  - server_password, server_login, notes — не трогаем никогда;
  - сервер с sync_locked=1 пропускается целиком (ручной режим важнее);
  - услуга, пропавшая у провайдера, помечается status='deleted', но не удаляется.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from app.connectors import ConnectorError, RemoteService, build_connector
from app.models import HostingAccount, Server
from app.onedash import CABINET_URL, onedash_addon_defaults
from app.repository import (
    create_server,
    get_account,
    list_auto_sync_accounts,
    servers_for_account,
    set_account_sync_result,
    update_account_urls,
    update_server_from_sync,
)

logger = logging.getLogger(__name__)

_AUTO_NAME_PATTERN = re.compile(r"^\d+(:\d+)?$")


@dataclass
class SyncResult:
    account_id: int
    account_name: str = ""
    status: str = "ok"
    message: str = ""
    created: int = 0
    updated: int = 0
    skipped: int = 0
    missing: list[str] = field(default_factory=list)
    changes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    @property
    def has_changes(self) -> bool:
        return bool(self.created or self.updated or self.missing)


def _new_server_payload(account: HostingAccount, service: RemoteService) -> dict[str, object]:
    next_date = service.next_payment_date or (date.today() + timedelta(days=30))
    panel_url = account.panel_url
    payment_url = service.payment_url or account.payment_url
    if account.integration_type == "onedash":
        if _is_stale_onedash_url(panel_url):
            panel_url = CABINET_URL
        if _is_stale_onedash_url(payment_url):
            payment_url = CABINET_URL
    return {
        "hosting_account_id": account.id,
        "name": service.name or service.service_id,
        "provider": account.provider,
        "ip_address": service.ip_address,
        "location": service.location or "",
        "server_login": "",
        "server_password": "",
        "service_id": service.service_id,
        "amount": service.amount or 0,
        "currency": service.currency or "RUB",
        "billing_period_days": service.billing_period_days or 30,
        "next_payment_date": next_date.isoformat(),
        "payment_url": payment_url,
        "panel_url": panel_url,
        "notes": "",
    }


def _needs_name_refresh(server: Server) -> bool:
    name = (server.name or "").strip()
    if not name:
        return True
    service_id = (server.service_id or "").strip()
    if service_id and name == service_id:
        return True
    return bool(_AUTO_NAME_PATTERN.match(name))


def _is_stale_onedash_url(url: str) -> bool:
    return "clientarea.php" in (url or "")


def _diff_fields(
    server: Server,
    service: RemoteService,
    *,
    account: HostingAccount,
) -> tuple[dict[str, object], list[str]]:
    fields: dict[str, object] = {}
    notes: list[str] = []
    if service.next_payment_date and service.next_payment_date != server.next_payment_date:
        fields["next_payment_date"] = service.next_payment_date.isoformat()
    if account.integration_type == "onedash":
        if service.amount is not None and (
            server.amount <= 0 or abs(service.amount - server.amount) > 0.001
        ):
            fields["amount"] = service.amount
            notes.append(f"{server.name}: сумма {server.amount:g} -> {service.amount:g}")
    elif service.amount is not None and abs(service.amount - server.amount) > 0.001:
        fields["amount"] = service.amount
        notes.append(f"{server.name}: сумма {server.amount:g} -> {service.amount:g}")
    if (
        account.integration_type == "onedash"
        and service.billing_period_days
        and (
            server.billing_period_days != service.billing_period_days
            or server.amount <= 0
        )
    ):
        fields["billing_period_days"] = service.billing_period_days
        notes.append(
            f"{server.name}: период {server.billing_period_days} -> {service.billing_period_days} дн."
        )
    elif (
        account.integration_type == "billmanager"
        and service.billing_period_days
        and server.billing_period_days != service.billing_period_days
    ):
        fields["billing_period_days"] = service.billing_period_days
        notes.append(
            f"{server.name}: период {server.billing_period_days} -> {service.billing_period_days} дн."
        )
    if service.currency and service.currency != server.currency:
        fields["currency"] = service.currency
        notes.append(f"{server.name}: валюта {server.currency} -> {service.currency}")
    if service.status and service.status != server.status:
        fields["status"] = service.status
        notes.append(f"{server.name}: статус {server.status} -> {service.status}")
    payment_url = service.payment_url or account.payment_url
    if account.integration_type == "onedash" and _is_stale_onedash_url(server.payment_url):
        payment_url = CABINET_URL
    if payment_url and payment_url != server.payment_url:
        fields["payment_url"] = payment_url
    panel_url = account.panel_url
    if account.integration_type == "onedash" and _is_stale_onedash_url(server.panel_url):
        panel_url = CABINET_URL
    if panel_url and panel_url != server.panel_url:
        fields["panel_url"] = panel_url
    if service.name and _needs_name_refresh(server) and service.name != server.name:
        fields["name"] = service.name
        notes.append(f"{server.name or server.service_id}: имя -> {service.name}")
    if service.ip_address and not server.ip_address:
        fields["ip_address"] = service.ip_address
    if service.location and not (server.location or "").strip():
        fields["location"] = service.location
    return fields, notes


def _refresh_onedash_account_urls(account: HostingAccount) -> HostingAccount:
    update_account_urls(account.id, CABINET_URL, CABINET_URL)
    refreshed = get_account(account.id)
    return refreshed or account


def sync_account(account_id: int, *, create_missing: bool = True) -> SyncResult:
    account = get_account(account_id)
    if account is None:
        return SyncResult(account_id=account_id, status="error", message="Аккаунт не найден.")
    result = SyncResult(account_id=account_id, account_name=account.name)

    connector = build_connector(account)
    if connector is None:
        result.status = "error"
        result.message = "Для ручного аккаунта синхронизация недоступна."
        return result

    if account.integration_type == "onedash" and (
        _is_stale_onedash_url(account.panel_url) or _is_stale_onedash_url(account.payment_url)
    ):
        account = _refresh_onedash_account_urls(account)

    existing = servers_for_account(account_id)
    known_periods = {
        server.service_id: server.billing_period_days
        for server in existing
        if server.service_id and server.billing_period_days >= 7
    }

    try:
        if account.integration_type == "onedash":
            remote_services = connector.list_services(
                known_periods=known_periods,
                addon_defaults=onedash_addon_defaults(account.integration_settings),
            )
        else:
            remote_services = connector.list_services()
    except ConnectorError as error:
        result.status = "error"
        result.message = str(error)
        set_account_sync_result(account_id, "error", result.message)
        logger.warning("Sync failed for account %s: %s", account_id, error)
        return result

    by_service_id = {server.service_id: server for server in existing if server.service_id}
    seen: set[str] = set()

    for service in remote_services:
        seen.add(service.service_id)
        server = by_service_id.get(service.service_id)
        if server is None:
            if create_missing:
                create_server(_new_server_payload(account, service))
                result.created += 1
                result.changes.append(f"Добавлен сервер: {service.name or service.service_id}")
            continue
        if server.sync_locked:
            result.skipped += 1
            continue
        fields, notes = _diff_fields(server, service, account=account)
        if fields:
            update_server_from_sync(server.id, fields)
            result.updated += 1
            result.changes.extend(notes)

    for service_id, server in by_service_id.items():
        if service_id in seen or server.sync_locked or server.status == "deleted":
            continue
        update_server_from_sync(server.id, {"status": "deleted"})
        result.missing.append(server.name)

    result.message = (
        f"Услуг получено: {len(remote_services)}. "
        f"Создано: {result.created}, обновлено: {result.updated}, "
        f"пропущено (заблокировано): {result.skipped}, пропало: {len(result.missing)}."
    )
    if account.integration_type == "onedash" and remote_services:
        defaults = onedash_addon_defaults(account.integration_settings)
        extras = []
        if defaults.get("static_ip"):
            extras.append("IP")
        if defaults.get("backup"):
            extras.append("бэкап")
        if defaults.get("nvme"):
            extras.append("NVMe")
        if str(defaults.get("processor") or "intel").lower() == "amd":
            extras.append("AMD")
        if extras:
            result.message += f" Доп. опции в расчёте: {', '.join(extras)}."
        else:
            result.message += " Доп. опции не заданы — суммы только по тарифу."
    set_account_sync_result(account_id, "ok", result.message)
    return result


def sync_due_accounts() -> list[SyncResult]:
    results: list[SyncResult] = []
    for account in list_auto_sync_accounts():
        try:
            results.append(sync_account(account.id))
        except Exception:  # noqa: BLE001 - один провайдер не должен ронять цикл
            logger.exception("Auto-sync crashed for account %s", account.id)
    return results
