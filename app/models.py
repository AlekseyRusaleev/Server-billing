from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from sqlite3 import Row

from app.crypto import decrypt_secret


@dataclass(frozen=True)
class HostingAccount:
    id: int
    name: str
    provider: str
    login: str
    auth_secret: str
    panel_url: str
    payment_url: str
    notes: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class PaymentHistoryItem:
    id: int
    server_id: int
    server_name: str
    provider: str
    amount: float
    currency: str
    paid_at: date
    previous_next_payment_date: date
    next_payment_date: date
    note: str
    created_at: str


@dataclass(frozen=True)
class Server:
    id: int
    hosting_account_id: int | None
    name: str
    provider: str
    ip_address: str
    location: str
    server_login: str
    server_password: str
    service_id: str
    amount: float
    currency: str
    billing_period_days: int
    next_payment_date: date
    payment_url: str
    panel_url: str
    notes: str
    status: str
    created_at: str
    updated_at: str
    last_paid_at: str
    account_name: str = ""
    account_login: str = ""
    account_secret: str = ""
    account_panel_url: str = ""
    account_payment_url: str = ""

    @property
    def days_left(self) -> int:
        return (self.next_payment_date - date.today()).days

    @property
    def payment_state(self) -> str:
        if self.days_left < 0:
            return "overdue"
        if self.days_left <= 3:
            return "urgent"
        if self.days_left <= 7:
            return "soon"
        return "ok"

    @property
    def payment_label(self) -> str:
        labels = {
            "overdue": "Просрочено",
            "urgent": "Срочно",
            "soon": "Скоро",
            "ok": "В порядке",
        }
        return labels[self.payment_state]

    @property
    def effective_payment_url(self) -> str:
        return self.payment_url or self.account_payment_url or self.account_panel_url or self.panel_url

    @property
    def effective_panel_url(self) -> str:
        return self.panel_url or self.account_panel_url


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def row_value(row: Row, key: str, default: object = "") -> object:
    return row[key] if key in row.keys() else default


def account_from_row(row: Row) -> HostingAccount:
    return HostingAccount(
        id=row["id"],
        name=row["name"],
        provider=row["provider"],
        login=row["login"] or "",
        auth_secret=decrypt_secret(row["auth_secret"] or ""),
        panel_url=row["panel_url"] or "",
        payment_url=row["payment_url"] or "",
        notes=row["notes"] or "",
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def server_from_row(row: Row) -> Server:
    return Server(
        id=row["id"],
        hosting_account_id=row["hosting_account_id"],
        name=row["name"],
        provider=row["provider"],
        ip_address=row["ip_address"] or "",
        location=str(row_value(row, "location", "") or ""),
        server_login=str(row_value(row, "server_login", "") or ""),
        server_password=decrypt_secret(str(row_value(row, "server_password", "") or "")),
        service_id=row["service_id"] or "",
        amount=float(row["amount"] or 0),
        currency=row["currency"],
        billing_period_days=int(row["billing_period_days"] or 30),
        next_payment_date=parse_date(row["next_payment_date"]),
        payment_url=row["payment_url"] or "",
        panel_url=row["panel_url"] or "",
        notes=row["notes"] or "",
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        last_paid_at=row["last_paid_at"] or "",
        account_name=str(row_value(row, "account_name", "") or ""),
        account_login=str(row_value(row, "account_login", "") or ""),
        account_secret=decrypt_secret(str(row_value(row, "account_secret", "") or "")),
        account_panel_url=str(row_value(row, "account_panel_url", "") or ""),
        account_payment_url=str(row_value(row, "account_payment_url", "") or ""),
    )


def payment_history_from_row(row: Row) -> PaymentHistoryItem:
    return PaymentHistoryItem(
        id=row["id"],
        server_id=row["server_id"],
        server_name=row["server_name"],
        provider=row["provider"],
        amount=float(row["amount"] or 0),
        currency=row["currency"],
        paid_at=parse_date(row["paid_at"]),
        previous_next_payment_date=parse_date(row["previous_next_payment_date"]),
        next_payment_date=parse_date(row["next_payment_date"]),
        note=row["note"] or "",
        created_at=row["created_at"],
    )
