from __future__ import annotations

import json
import logging
import time
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

from app.config import settings
from app.db import connect, database_path
from app.models import Server
from app.repository import (
    get_app_setting,
    get_last_backup_at,
    list_servers,
    mark_backup_sent,
    mark_ssl_alert_sent,
    notification_settings,
    ssl_alert_already_sent,
)
from app.provider_sync import SyncResult, sync_due_accounts
from app.sslcheck import SslResult, run_all as run_ssl_checks
from app.telegram import build_payment_deeplink

logger = logging.getLogger(__name__)


def reminder_days() -> set[int]:
    raw = notification_settings().get("reminder_days", settings.reminder_days)
    days: set[int] = set()
    for value in raw.split(","):
        value = value.strip()
        if value:
            days.add(int(value))
    return days


def ensure_notification_log() -> None:
    with connect() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS notification_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                server_id INTEGER NOT NULL,
                channel TEXT NOT NULL,
                reminder_key TEXT NOT NULL,
                sent_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(server_id, channel, reminder_key)
            )
            """
        )


def was_sent(server: Server, reminder_key: str) -> bool:
    ensure_notification_log()
    with connect() as connection:
        row = connection.execute(
            """
            SELECT id FROM notification_log
            WHERE server_id = ? AND channel = 'telegram' AND reminder_key = ?
            """,
            (server.id, reminder_key),
        ).fetchone()
    return row is not None


def mark_sent(server: Server, reminder_key: str) -> None:
    ensure_notification_log()
    with connect() as connection:
        connection.execute(
            """
            INSERT OR IGNORE INTO notification_log (server_id, channel, reminder_key)
            VALUES (?, 'telegram', ?)
            """,
            (server.id, reminder_key),
        )


def reminder_key(server: Server) -> str:
    return f"{server.next_payment_date.isoformat()}:{server.days_left}"


def build_message(server: Server) -> str:
    amount = f"{server.amount:g} {server.currency}"
    due = server.next_payment_date.strftime("%d.%m.%Y")
    if server.days_left < 0:
        state = f"просрочено на {-server.days_left} дн."
    elif server.days_left == 0:
        state = "оплата сегодня"
    else:
        state = f"осталось {server.days_left} дн."

    return (
        "Скоро оплата сервера\n\n"
        f"{server.name}\n"
        f"Провайдер: {server.provider}\n"
        f"Аккаунт: {server.account_name or 'не привязан'}\n"
        f"IP: {server.ip_address or 'не указан'}\n"
        f"Сумма: {amount}\n"
        f"Оплатить до: {due} ({state})\n\n"
        f"Открыть оплату: {build_payment_deeplink(server)}"
    )


def send_telegram(text: str) -> bool:
    current_settings = notification_settings()
    token = current_settings.get("telegram_bot_token", "").strip()
    chat_id = current_settings.get("telegram_chat_id", "").strip()
    if not token or not chat_id:
        logger.info("Telegram token or chat id is empty; reminders are disabled.")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": "true",
        }
    ).encode()
    request = urllib.request.Request(url, data=payload, method="POST")
    with urllib.request.urlopen(request, timeout=20) as response:
        body = response.read().decode("utf-8")
        parsed = json.loads(body)
        if not parsed.get("ok"):
            raise RuntimeError(body)
    return True


def send_telegram_document(path: Path, caption: str = "") -> bool:
    current_settings = notification_settings()
    token = current_settings.get("telegram_bot_token", "").strip()
    chat_id = current_settings.get("telegram_chat_id", "").strip()
    if not token or not chat_id:
        logger.info("Telegram token or chat id is empty; backup delivery is disabled.")
        return False

    boundary = "----server-billing-boundary"
    file_bytes = path.read_bytes()
    fields = [
        (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="chat_id"\r\n\r\n'
            f"{chat_id}\r\n"
        ).encode(),
        (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="caption"\r\n\r\n'
            f"{caption}\r\n"
        ).encode(),
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="document"; filename="{path.name}"\r\n'
            "Content-Type: application/octet-stream\r\n\r\n"
        ).encode(),
        file_bytes,
        f"\r\n--{boundary}--\r\n".encode(),
    ]
    body = b"".join(fields)
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendDocument",
        data=body,
        method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        parsed = json.loads(response.read().decode("utf-8"))
        if not parsed.get("ok"):
            raise RuntimeError(parsed)
    return True


def send_backup() -> bool:
    path = database_path()
    if not path.exists():
        logger.warning("Database file does not exist: %s", path)
        return False
    sent = send_telegram_document(
        path,
        caption=f"Server Billing backup: {date.today().isoformat()}",
    )
    if sent:
        mark_backup_sent()
    return sent


def send_due_backup() -> bool:
    current_settings = notification_settings()
    interval = int(current_settings.get("backup_interval_days", "7") or "0")
    if interval <= 0:
        return False
    last_backup_at = get_last_backup_at()
    if last_backup_at is not None and (date.today() - last_backup_at).days < interval:
        return False
    return send_backup()


def send_due_reminders() -> int:
    due_days = reminder_days()
    sent = 0
    for server in list_servers():
        if server.days_left not in due_days and server.days_left >= 0:
            continue
        if server.days_left < 0 and -1 not in due_days:
            continue

        key = reminder_key(server)
        if was_sent(server, key):
            continue

        if send_telegram(build_message(server)):
            mark_sent(server, key)
            sent += 1
    return sent


def build_sync_message(result: SyncResult) -> str:
    lines = [f"Синхронизация: {result.account_name}", result.message]
    if result.changes:
        lines.append("")
        lines.extend(result.changes[:15])
    if result.missing:
        lines.append("")
        lines.append("Пропали у провайдера: " + ", ".join(result.missing[:15]))
    return "\n".join(lines)


def run_provider_sync() -> int:
    """Запускает автосинхронизацию и уведомляет в Telegram об изменениях."""
    notified = 0
    for result in sync_due_accounts():
        if result.ok and result.has_changes:
            try:
                if send_telegram(build_sync_message(result)):
                    notified += 1
            except Exception:
                logger.exception("Failed to send sync notification for account %s", result.account_id)
        elif not result.ok:
            logger.warning("Sync error for %s: %s", result.account_name, result.message)
    return notified


def ssl_alert_threshold() -> int:
    try:
        return int(get_app_setting("ssl_alert_days", "3") or 3)
    except ValueError:
        return 3


def build_ssl_message(result: SslResult) -> str:
    if result.days_left is not None and result.days_left < 0:
        state = f"истёк {-result.days_left} дн. назад"
    elif result.days_left == 0:
        state = "истекает сегодня"
    else:
        state = f"осталось {result.days_left} дн."
    return (
        "SSL-сертификат скоро истекает\n\n"
        f"{result.label}\n"
        f"Домен: {result.host}:{result.port}\n"
        f"Действует до: {result.expiry}\n"
        f"Статус: {state}"
    )


def check_ssl_certificates() -> int:
    """Проверяет SSL-сертификаты целей и шлёт уведомления при скором истечении."""
    threshold = ssl_alert_threshold()
    today = date.today().isoformat()
    sent = 0
    for result in run_ssl_checks():
        if not result.ok or result.days_left is None or result.days_left > threshold:
            continue
        alert_key = f"{result.expiry}:{today}"
        if ssl_alert_already_sent(result.host, alert_key):
            continue
        try:
            if send_telegram(build_ssl_message(result)):
                mark_ssl_alert_sent(result.host, alert_key)
                sent += 1
        except Exception:
            logger.exception("Failed to send SSL alert for %s", result.host)
    return sent


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logger.info("Reminder worker started.")
    while True:
        try:
            synced = run_provider_sync()
            sent = send_due_reminders()
            ssl_alerts = check_ssl_certificates()
            backup_sent = send_due_backup()
            logger.info(
                "Check finished. Synced accounts notified: %s. Reminders sent: %s. SSL alerts: %s. Backup sent: %s. Date: %s",
                synced,
                sent,
                ssl_alerts,
                backup_sent,
                date.today(),
            )
        except Exception:
            logger.exception("Scheduled check failed.")
        interval = int(notification_settings().get("check_interval_seconds", 86400))
        time.sleep(interval)


if __name__ == "__main__":
    main()
