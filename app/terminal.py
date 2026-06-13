"""Веб-терминал: мост между WebSocket в браузере и SSH-сессией.

Безопасность:
- SSH-подключение идёт с сервера панели, не с компьютера пользователя.
- Пароль расшифровывается только на сервере и никогда не уходит в браузер.
- WebSocket авторизуется вручную по сессионной cookie (HTTP-middleware
  на WebSocket-соединения не распространяется).
- Проверка ключа хоста по схеме TOFU (trust on first use): при смене ключа
  соединение разрывается с предупреждением о возможной MITM-атаке.
- Неактивная сессия автоматически закрывается через IDLE_TIMEOUT_SECONDS.
- Команды не логируются.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import WebSocket, WebSocketDisconnect

from app.auth import COOKIE_NAME, auth_enabled, verify_session_token
from app.config import settings
from app.db import database_path
from app.ip_access import is_websocket_ip_allowed
from app.repository import get_app_setting, get_server

WEB_TERMINAL_FLAG = "web_terminal_enabled"
IDLE_TIMEOUT_SECONDS = 15 * 60
CONNECT_TIMEOUT_SECONDS = 15
OUTPUT_CHUNK = 16 * 1024


def web_terminal_enabled() -> bool:
    return get_app_setting(WEB_TERMINAL_FLAG, "") == "1"


def _known_hosts_path() -> Path:
    return database_path().parent / "ssh_known_hosts.json"


def _load_known_hosts() -> dict[str, str]:
    path = _known_hosts_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text("utf-8"))
    except Exception:
        return {}


def _remember_host(host_id: str, fingerprint: str) -> None:
    data = _load_known_hosts()
    data[host_id] = fingerprint
    path = _known_hosts_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), "utf-8")


def _authenticated(websocket: WebSocket) -> bool:
    if not auth_enabled():
        return False
    token = websocket.cookies.get(COOKIE_NAME, "")
    payload = verify_session_token(token)
    return bool(payload and payload.get("sub") == settings.admin_username)


async def _notify(websocket: WebSocket, kind: str, message: str) -> None:
    try:
        await websocket.send_text(json.dumps({"type": kind, "message": message}))
    except Exception:
        pass


async def terminal_websocket(websocket: WebSocket, server_id: int) -> None:
    await websocket.accept()

    if not _authenticated(websocket):
        await _notify(websocket, "error", "Не авторизовано. Войдите в панель заново.")
        await websocket.close(code=4401)
        return

    if not is_websocket_ip_allowed(websocket):
        await _notify(websocket, "error", "Доступ с вашего IP-адреса запрещён.")
        await websocket.close(code=4403)
        return

    if not web_terminal_enabled():
        await _notify(websocket, "error", "Веб-терминал выключен в настройках панели.")
        await websocket.close(code=4403)
        return

    server = get_server(server_id)
    if server is None:
        await _notify(websocket, "error", "Сервер не найден.")
        await websocket.close(code=4404)
        return

    host = server.ip_address.strip()
    username = server.server_login.strip()
    password = server.server_password
    port = server.ssh_port or 22
    if not host or not username or not password:
        await _notify(websocket, "error", "Нет IP, логина или пароля сервера — терминал недоступен.")
        await websocket.close(code=4400)
        return

    try:
        import asyncssh
    except ImportError:
        await _notify(websocket, "error", "Модуль asyncssh не установлен на сервере панели.")
        await websocket.close(code=4500)
        return

    await _notify(websocket, "status", f"Подключение к {host}:{port}…")

    try:
        conn = await asyncio.wait_for(
            asyncssh.connect(
                host,
                port=port,
                username=username,
                password=password,
                known_hosts=None,
                client_keys=None,
                config=None,
            ),
            timeout=CONNECT_TIMEOUT_SECONDS,
        )
    except asyncio.TimeoutError:
        await _notify(websocket, "error", f"Таймаут подключения к {host}:{port}.")
        await websocket.close()
        return
    except (OSError, asyncssh.Error) as exc:
        await _notify(websocket, "error", f"Не удалось подключиться: {exc}")
        await websocket.close()
        return

    host_id = f"{host}:{port}"
    host_key = conn.get_server_host_key()
    fingerprint = host_key.get_fingerprint() if host_key else ""
    known = _load_known_hosts()
    stored = known.get(host_id)
    if stored is None:
        _remember_host(host_id, fingerprint)
        await _notify(websocket, "status", f"Новый сервер, ключ запомнен (TOFU): {fingerprint}")
    elif stored != fingerprint:
        conn.close()
        await _notify(
            websocket,
            "error",
            "ВНИМАНИЕ: ключ хоста изменился — возможна MITM-атака. Подключение прервано. "
            "Если вы переустанавливали сервер, удалите старый ключ из data/ssh_known_hosts.json.",
        )
        await websocket.close(code=4495)
        return

    try:
        await _bridge(websocket, conn, asyncssh)
    finally:
        conn.close()


async def _bridge(websocket: WebSocket, conn, asyncssh) -> None:
    process = await conn.create_process(
        term_type="xterm-256color",
        term_size=(80, 24),
        stderr=asyncssh.STDOUT,
        encoding=None,
    )
    loop = asyncio.get_event_loop()
    last_input = loop.time()
    closed = asyncio.Event()

    async def ssh_to_browser() -> None:
        try:
            while True:
                data = await process.stdout.read(OUTPUT_CHUNK)
                if not data:
                    break
                await websocket.send_bytes(data)
        except Exception:
            pass
        finally:
            closed.set()

    async def browser_to_ssh() -> None:
        nonlocal last_input
        try:
            while True:
                raw = await websocket.receive_text()
                try:
                    message = json.loads(raw)
                except Exception:
                    continue
                kind = message.get("type")
                if kind == "input":
                    last_input = loop.time()
                    process.stdin.write(str(message.get("data", "")).encode("utf-8"))
                elif kind == "resize":
                    try:
                        cols = max(2, int(message.get("cols", 80)))
                        rows = max(1, int(message.get("rows", 24)))
                        process.change_terminal_size(cols, rows)
                    except Exception:
                        pass
        except WebSocketDisconnect:
            pass
        except Exception:
            pass
        finally:
            closed.set()

    async def idle_watchdog() -> None:
        while not closed.is_set():
            await asyncio.sleep(30)
            if loop.time() - last_input > IDLE_TIMEOUT_SECONDS:
                await _notify(websocket, "status", "Сессия закрыта из-за бездействия.")
                break
        closed.set()

    tasks = [
        asyncio.create_task(ssh_to_browser()),
        asyncio.create_task(browser_to_ssh()),
        asyncio.create_task(idle_watchdog()),
    ]
    try:
        await closed.wait()
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        try:
            process.close()
        except Exception:
            pass
        try:
            await websocket.close()
        except Exception:
            pass
