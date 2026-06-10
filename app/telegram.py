from __future__ import annotations

import json
import urllib.request
from urllib.parse import quote_plus

from app.config import settings
from app.models import Server
from app.repository import get_effective_setting


def build_payment_deeplink(server: Server) -> str:
    base_url = get_effective_setting("base_url", settings.base_url).rstrip("/")
    return f"{base_url}/servers/{server.id}/pay"


def build_reminder_text(server: Server) -> str:
    amount = f"{server.amount:g} {server.currency}"
    due = server.next_payment_date.strftime("%d.%m.%Y")
    return (
        "Скоро оплата сервера\n\n"
        f"{server.name}\n"
        f"Провайдер: {server.provider}\n"
        f"IP: {server.ip_address or 'не указан'}\n"
        f"Сумма: {amount}\n"
        f"Оплатить до: {due}\n"
        f"Осталось дней: {server.days_left}\n\n"
        f"Открыть оплату: {build_payment_deeplink(server)}"
    )


def build_telegram_share_url(server: Server) -> str:
    text = quote_plus(build_reminder_text(server))
    return f"https://t.me/share/url?url={quote_plus(build_payment_deeplink(server))}&text={text}"


def telegram_get(token: str, method: str) -> dict[str, object]:
    url = f"https://api.telegram.org/bot{token}/{method}"
    with urllib.request.urlopen(url, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not payload.get("ok"):
        raise RuntimeError(str(payload))
    return payload


def telegram_bot_username(token: str) -> str:
    payload = telegram_get(token, "getMe")
    result = payload.get("result") or {}
    username = result.get("username") if isinstance(result, dict) else ""
    return f"@{username}" if username else "подключен"


def detect_telegram_chats(token: str) -> list[dict[str, str]]:
    payload = telegram_get(token, "getUpdates")
    updates = payload.get("result") or []
    chats: dict[str, dict[str, str]] = {}
    for item in updates:
        if not isinstance(item, dict):
            continue
        message = item.get("message") or item.get("channel_post") or {}
        if not isinstance(message, dict):
            continue
        chat = message.get("chat") or {}
        if not isinstance(chat, dict) or chat.get("id") is None:
            continue
        chat_id = str(chat["id"])
        title = chat.get("title") or chat.get("username") or "личный чат"
        chat_type = chat.get("type") or "chat"
        chats[chat_id] = {"id": chat_id, "title": str(title), "type": str(chat_type)}
    return list(chats.values())
