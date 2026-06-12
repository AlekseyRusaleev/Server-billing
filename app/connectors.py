"""Слой интеграций с биллингом провайдеров.

Панель остаётся менеджером оплат: коннекторы только читают данные у провайдера
(услуги, даты, суммы, статусы) и не проводят платежи. Каждый тип интеграции
реализует один общий интерфейс, поэтому добавление нового провайдера не трогает
остальную панель.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable

from app.models import HostingAccount


class ConnectorError(RuntimeError):
    """Любая ошибка обращения к биллингу провайдера (сеть, авторизация, формат)."""


@dataclass(frozen=True)
class RemoteService:
    """Нормализованное представление услуги у провайдера.

    Поля, которые провайдер не отдаёт, остаются пустыми/None — синхронизация
    обновляет только то, что реально пришло.
    """

    service_id: str
    name: str = ""
    ip_address: str = ""
    status: str = "active"
    next_payment_date: date | None = None
    amount: float | None = None
    currency: str = ""
    payment_url: str = ""
    billing_period_days: int | None = None
    location: str = ""


@runtime_checkable
class ProviderConnector(Protocol):
    """Контракт для всех интеграций с провайдерами."""

    def test_connection(self) -> None:
        """Проверяет доступность и авторизацию. Бросает ConnectorError при ошибке."""

    def list_services(self) -> list[RemoteService]:
        """Возвращает список услуг клиента у провайдера."""


def build_connector(account: HostingAccount) -> ProviderConnector | None:
    """Возвращает коннектор под тип интеграции аккаунта или None для ручного режима."""
    if account.integration_type == "billmanager":
        from app.billmanager import BillmanagerConnector

        return BillmanagerConnector(
            login=account.login,
            password=account.auth_secret,
            integration_url=account.integration_url,
            panel_url=account.panel_url,
            provider=account.provider,
        )
    if account.integration_type == "onedash":
        from app.onedash import OneDashConnector

        return OneDashConnector(
            api_key=account.auth_secret,
            api_base=account.integration_url or "",
        )
    return None
