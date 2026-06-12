from __future__ import annotations

import calendar as calendar_lib
import json
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from fastapi import FastAPI, Form, HTTPException, Request, WebSocket
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.auth import COOKIE_NAME, check_login, create_session_token, hash_password, is_authenticated
from app.config import settings
from app.db import init_db
from app.repository import (
    add_manual_payment,
    create_account,
    create_server,
    create_ssl_monitor,
    delete_account,
    delete_payment,
    delete_server,
    delete_ssl_monitor,
    encrypt_existing_secrets,
    get_account,
    get_app_setting,
    get_server,
    list_payment_history,
    list_accounts,
    list_servers,
    list_ssl_monitors,
    mark_paid,
    monthly_expense_summary,
    monthly_plan_summary,
    notification_settings,
    provider_expense_summary,
    refresh_currency_rates,
    save_currency_settings,
    save_notification_settings,
    set_app_setting,
    seed_demo_data,
    set_account_sync_result,
    update_account,
    update_server,
)
from app.reminders import send_backup, send_due_reminders, send_telegram
from app.connectors import ConnectorError, build_connector
from app.provider_sync import sync_account
from app.catalog_sync import catalog_sync_status, sync_provider_catalog
from app.provider_templates import list_provider_templates, provider_catalog_meta, provider_countries
from app.system_update import start_system_update
from app.sslcheck import run_all as run_ssl_checks
from app.terminal import terminal_websocket, web_terminal_enabled
from app.telegram import (
    build_telegram_share_url,
    detect_telegram_chats,
    ensure_telegram_polling,
    telegram_bot_link,
    telegram_bot_username,
)
from app.integrations import INTEGRATION_OPTIONS, SUPPORTED_INTEGRATIONS
from app.billmanager import resolve_billmanager_url
from app.onedash import build_onedash_integration_settings, onedash_addon_defaults
from app.version import current_version

app = FastAPI(title=settings.app_name)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

SUPPORTED_CURRENCIES = {"RUB", "USD", "EUR", "USDT"}
RU_MONTHS = [
    "",
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]
RU_WEEKDAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
DONATION_URL = "https://t.me/AlekseyRdonate_bot"
templates.env.globals["donation_url"] = DONATION_URL


def _load_detected_telegram_chats() -> list[dict[str, str]]:
    raw = get_app_setting("telegram_detected_chats", "")
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict) and item.get("id")]


def _clear_detected_telegram_chats() -> None:
    set_app_setting("telegram_detected_chats", "")


def _save_detected_telegram_chats(chats: list[dict[str, str]]) -> None:
    set_app_setting("telegram_detected_chats", json.dumps(chats, ensure_ascii=False))


@app.on_event("startup")
def startup() -> None:
    init_db()
    seed_demo_data()
    encrypt_existing_secrets()


@app.middleware("http")
async def require_login(request: Request, call_next):
    public_prefixes = ("/static/",)
    public_paths = {"/login"}
    if request.url.path not in public_paths and not request.url.path.startswith(public_prefixes):
        if not is_authenticated(request):
            return RedirectResponse("/login", status_code=303)
    return await call_next(request)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> HTMLResponse:
    if is_authenticated(request):
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse("login.html", {"request": request, "error": ""})


@app.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
):
    if not check_login(username.strip(), password):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Неверный логин или пароль"},
            status_code=401,
        )
    response = RedirectResponse("/", status_code=303)
    is_secure = (
        request.url.scheme == "https"
        or request.headers.get("x-forwarded-proto", "").lower() == "https"
    )
    response.set_cookie(
        COOKIE_NAME,
        create_session_token(username.strip()),
        httponly=True,
        secure=is_secure,
        samesite="lax",
        max_age=60 * 60 * 24 * 30,
    )
    return response


@app.post("/logout")
def logout() -> RedirectResponse:
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(COOKIE_NAME)
    return response


def form_payload(
    hosting_account_id: int,
    name: str,
    provider: str,
    ip_address: str,
    location: str,
    server_login: str,
    server_password: str,
    service_id: str,
    amount: float,
    currency: str,
    billing_period_days: int,
    next_payment_date: str,
    payment_url: str,
    panel_url: str,
    notes: str,
    sync_locked: bool = False,
    ssh_port: int = 22,
    ssl_host: str = "",
) -> dict[str, object]:
    try:
        normalized_port = int(ssh_port)
    except (TypeError, ValueError):
        normalized_port = 22
    if normalized_port < 1 or normalized_port > 65535:
        normalized_port = 22
    normalized_currency = currency.strip().upper() or "RUB"
    if normalized_currency not in SUPPORTED_CURRENCIES:
        normalized_currency = "RUB"
    return {
        "hosting_account_id": hosting_account_id or None,
        "name": name.strip(),
        "provider": provider.strip(),
        "ip_address": ip_address.strip(),
        "location": location.strip(),
        "server_login": server_login.strip(),
        "server_password": server_password.strip(),
        "ssh_port": normalized_port,
        "ssl_host": ssl_host.strip(),
        "service_id": service_id.strip(),
        "amount": amount,
        "currency": normalized_currency,
        "billing_period_days": billing_period_days,
        "next_payment_date": parse_payment_date(next_payment_date),
        "payment_url": payment_url.strip(),
        "panel_url": panel_url.strip(),
        "notes": notes.strip(),
        "sync_locked": bool(sync_locked),
    }


def parse_payment_date(value: str) -> str:
    cleaned = value.strip()
    for date_format in ("%d.%m.%Y", "%d-%m-%Y", "%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(cleaned, date_format).date().isoformat()
        except ValueError:
            continue
    raise HTTPException(status_code=400, detail="Дата должна быть в формате дд.мм.гггг")


def account_payload(
    name: str,
    provider: str,
    login: str,
    auth_secret: str,
    panel_url: str,
    payment_url: str,
    notes: str,
    integration_type: str = "manual",
    integration_url: str = "",
    auto_sync_enabled: bool = False,
    *,
    onedash_static_ip: bool = True,
    onedash_backup: bool = True,
    onedash_nvme: bool = False,
    onedash_processor: str = "intel",
    integration_settings: str = "{}",
) -> dict[str, object]:
    normalized_integration = integration_type.strip().lower() or "manual"
    if normalized_integration not in SUPPORTED_INTEGRATIONS:
        normalized_integration = "manual"
    settings_json = integration_settings or "{}"
    if normalized_integration == "onedash":
        settings_json = build_onedash_integration_settings(
            static_ip=onedash_static_ip,
            backup=onedash_backup,
            nvme=onedash_nvme,
            processor=onedash_processor,
        )
    panel = panel_url.strip()
    payment = payment_url.strip()
    api_url = integration_url.strip()
    if normalized_integration == "billmanager":
        billmgr = resolve_billmanager_url(api_url, panel, provider)
        if billmgr:
            api_url = billmgr
            cabinet = f"{billmgr}?func=logon"
            if not panel:
                panel = cabinet
            if not payment:
                payment = cabinet
    return {
        "name": name.strip(),
        "provider": provider.strip(),
        "login": login.strip(),
        "auth_secret": auth_secret.strip(),
        "panel_url": panel,
        "payment_url": payment,
        "notes": notes.strip(),
        "integration_type": normalized_integration,
        "integration_url": api_url,
        "auto_sync_enabled": bool(auto_sync_enabled) and normalized_integration != "manual",
        "integration_settings": settings_json,
    }


def account_form_options(accounts) -> list[dict[str, object]]:
    return [
        {
            "id": account.id,
            "name": account.name,
            "provider": account.provider,
            "login": account.login,
            "panel_url": account.panel_url,
            "payment_url": account.payment_url,
        }
        for account in accounts
    ]


@app.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    q: str = "",
    provider: str = "",
    state: str = "",
) -> HTMLResponse:
    all_servers = list_servers()
    servers = list_servers(search=q, provider=provider, payment_state=state)
    accounts = list_accounts()
    monthly_plan = monthly_plan_summary(all_servers)
    due_7 = [server for server in all_servers if server.days_left <= 7]
    overdue = [server for server in all_servers if server.days_left < 0]
    providers = sorted({server.provider for server in all_servers})
    current_notifications = notification_settings()
    bot_ready = bool(current_notifications.get("telegram_bot_token"))
    chat_ready = bool(current_notifications.get("telegram_chat_id"))
    onboarding = [
        {"label": "Добавить Telegram bot", "done": bot_ready, "href": "/settings#telegram-setup"},
        {"label": "Добавить Telegram chat", "done": chat_ready, "href": "/settings#telegram-setup"},
        {"label": "Добавить аккаунт провайдера", "done": bool(accounts), "href": "/accounts"},
        {
            "label": "Добавить первый сервер",
            "done": bool(all_servers),
            "href": "#add-server",
            "action": "open-server-modal",
        },
        {
            "label": "Отправить тест",
            "done": bool(current_notifications.get("telegram_tested_at")),
            "href": "/settings#telegram-check",
        },
    ]
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "servers": servers,
            "accounts": accounts,
            "account_options": account_form_options(accounts),
            "providers": providers,
            "filters": {"q": q, "provider": provider, "state": state},
            "monthly_plan": monthly_plan,
            "onboarding": onboarding,
            "donation_url": DONATION_URL,
            "provider_templates": list_provider_templates(),
            "today": date.today(),
            "web_terminal_enabled": web_terminal_enabled(),
            "stats": {
                "total": len(servers),
                "due_7": len(due_7),
                "overdue": len(overdue),
                "monthly_rub": monthly_plan["total_base"],
            },
        },
    )


@app.post("/servers")
def add_server(
    hosting_account_id: int = Form(0),
    name: str = Form(...),
    provider: str = Form(...),
    ip_address: str = Form(""),
    location: str = Form(""),
    server_login: str = Form(""),
    server_password: str = Form(""),
    service_id: str = Form(""),
    amount: float = Form(0),
    currency: str = Form("RUB"),
    billing_period_days: int = Form(30),
    next_payment_date: str = Form(...),
    payment_url: str = Form(""),
    panel_url: str = Form(""),
    notes: str = Form(""),
    sync_locked: bool = Form(False),
    ssh_port: int = Form(22),
    ssl_host: str = Form(""),
) -> RedirectResponse:
    create_server(
        form_payload(
            hosting_account_id,
            name,
            provider,
            ip_address,
            location,
            server_login,
            server_password,
            service_id,
            amount,
            currency,
            billing_period_days,
            next_payment_date,
            payment_url,
            panel_url,
            notes,
            sync_locked,
            ssh_port,
            ssl_host,
        )
    )
    return RedirectResponse("/", status_code=303)


@app.get("/servers/{server_id}/edit", response_class=HTMLResponse)
def edit_server(request: Request, server_id: int) -> HTMLResponse:
    server = get_server(server_id)
    if server is None:
        raise HTTPException(status_code=404)
    accounts = list_accounts()
    return templates.TemplateResponse(
        "edit.html",
        {
            "request": request,
            "server": server,
            "accounts": accounts,
            "account_options": account_form_options(accounts),
            "provider_templates": list_provider_templates(),
            "integration_options": INTEGRATION_OPTIONS,
            "donation_url": DONATION_URL,
            "web_terminal_enabled": web_terminal_enabled(),
        },
    )


@app.post("/servers/{server_id}/edit")
def save_server(
    server_id: int,
    hosting_account_id: int = Form(0),
    name: str = Form(...),
    provider: str = Form(...),
    ip_address: str = Form(""),
    location: str = Form(""),
    server_login: str = Form(""),
    server_password: str = Form(""),
    service_id: str = Form(""),
    amount: float = Form(0),
    currency: str = Form("RUB"),
    billing_period_days: int = Form(30),
    next_payment_date: str = Form(...),
    payment_url: str = Form(""),
    panel_url: str = Form(""),
    notes: str = Form(""),
    sync_locked: bool = Form(False),
    ssh_port: int = Form(22),
    ssl_host: str = Form(""),
) -> RedirectResponse:
    payload = form_payload(
        hosting_account_id,
        name,
        provider,
        ip_address,
        location,
        server_login,
        server_password,
        service_id,
        amount,
        currency,
        billing_period_days,
        next_payment_date,
        payment_url,
        panel_url,
        notes,
        sync_locked,
        ssh_port,
        ssl_host,
    )
    if not server_password.strip():
        existing = get_server(server_id)
        if existing is not None:
            payload["server_password"] = existing.server_password
    update_server(server_id, payload)
    return RedirectResponse("/", status_code=303)


@app.post("/servers/{server_id}/paid")
def paid(server_id: int, note: str = Form("")) -> RedirectResponse:
    mark_paid(server_id, note=note)
    return RedirectResponse(f"/servers/{server_id}/pay", status_code=303)


@app.post("/servers/{server_id}/payments")
def add_payment(
    server_id: int,
    paid_at: str = Form(...),
    amount: float = Form(0),
    currency: str = Form(""),
    note: str = Form(""),
) -> RedirectResponse:
    if get_server(server_id) is None:
        raise HTTPException(status_code=404)
    add_manual_payment(server_id, parse_payment_date(paid_at), amount, currency, note)
    return RedirectResponse(f"/servers/{server_id}/pay", status_code=303)


@app.post("/servers/{server_id}/payments/{payment_id}/delete")
def remove_payment(server_id: int, payment_id: int) -> RedirectResponse:
    delete_payment(payment_id)
    return RedirectResponse(f"/servers/{server_id}/pay", status_code=303)


@app.post("/servers/{server_id}/delete")
def remove_server(server_id: int) -> RedirectResponse:
    delete_server(server_id)
    return RedirectResponse("/", status_code=303)


@app.get("/accounts", response_class=HTMLResponse)
def accounts_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "accounts.html",
        {
            "request": request,
            "accounts": list_accounts(),
            "provider_templates": list_provider_templates(),
            "integration_options": INTEGRATION_OPTIONS,
            "donation_url": DONATION_URL,
            "synced": request.query_params.get("synced", ""),
            "test": request.query_params.get("test", ""),
        },
    )


@app.post("/accounts")
def add_account(
    name: str = Form(...),
    provider: str = Form(...),
    login: str = Form(""),
    auth_secret: str = Form(""),
    panel_url: str = Form(""),
    payment_url: str = Form(""),
    notes: str = Form(""),
    integration_type: str = Form("manual"),
    integration_url: str = Form(""),
    auto_sync_enabled: bool = Form(False),
    onedash_static_ip: bool = Form(False),
    onedash_backup: bool = Form(False),
    onedash_nvme: bool = Form(False),
    onedash_processor: str = Form("intel"),
) -> RedirectResponse:
    create_account(
        account_payload(
            name,
            provider,
            login,
            auth_secret,
            panel_url,
            payment_url,
            notes,
            integration_type,
            integration_url,
            auto_sync_enabled,
            onedash_static_ip=onedash_static_ip or integration_type.strip().lower() == "onedash",
            onedash_backup=onedash_backup or integration_type.strip().lower() == "onedash",
            onedash_nvme=onedash_nvme,
            onedash_processor=onedash_processor,
        )
    )
    return RedirectResponse("/accounts", status_code=303)


@app.get("/accounts/{account_id}/edit", response_class=HTMLResponse)
def edit_account(request: Request, account_id: int) -> HTMLResponse:
    account = get_account(account_id)
    if account is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        "account_edit.html",
        {
            "request": request,
            "account": account,
            "provider_templates": list_provider_templates(),
            "integration_options": INTEGRATION_OPTIONS,
            "onedash_settings": onedash_addon_defaults(account.integration_settings),
            "donation_url": DONATION_URL,
        },
    )


@app.post("/accounts/{account_id}/edit")
def save_account(
    account_id: int,
    name: str = Form(...),
    provider: str = Form(...),
    login: str = Form(""),
    auth_secret: str = Form(""),
    panel_url: str = Form(""),
    payment_url: str = Form(""),
    notes: str = Form(""),
    integration_type: str = Form("manual"),
    integration_url: str = Form(""),
    auto_sync_enabled: bool = Form(False),
    onedash_static_ip: bool = Form(False),
    onedash_backup: bool = Form(False),
    onedash_nvme: bool = Form(False),
    onedash_processor: str = Form("intel"),
) -> RedirectResponse:
    existing = get_account(account_id)
    payload = account_payload(
        name,
        provider,
        login,
        auth_secret,
        panel_url,
        payment_url,
        notes,
        integration_type,
        integration_url,
        auto_sync_enabled,
        onedash_static_ip=onedash_static_ip,
        onedash_backup=onedash_backup,
        onedash_nvme=onedash_nvme,
        onedash_processor=onedash_processor,
        integration_settings=existing.integration_settings if existing else "{}",
    )
    if not auth_secret.strip():
        existing = get_account(account_id)
        if existing is not None:
            payload["auth_secret"] = existing.auth_secret
    update_account(account_id, payload)
    return RedirectResponse("/accounts", status_code=303)


@app.post("/accounts/{account_id}/delete")
def remove_account(account_id: int) -> RedirectResponse:
    delete_account(account_id)
    return RedirectResponse("/accounts", status_code=303)


@app.post("/accounts/{account_id}/sync")
def sync_account_route(account_id: int) -> RedirectResponse:
    if get_account(account_id) is None:
        raise HTTPException(status_code=404)
    result = sync_account(account_id)
    return RedirectResponse(
        f"/accounts?synced={'ok' if result.ok else 'error'}", status_code=303
    )


@app.post("/accounts/{account_id}/test-connection")
def test_account_connection(account_id: int) -> RedirectResponse:
    account = get_account(account_id)
    if account is None:
        raise HTTPException(status_code=404)
    connector = build_connector(account)
    if connector is None:
        return RedirectResponse("/accounts?test=manual", status_code=303)
    try:
        connector.test_connection()
    except ConnectorError as error:
        set_account_sync_result(account_id, "error", str(error))
        return RedirectResponse("/accounts?test=error", status_code=303)
    set_account_sync_result(account_id, "ok", "Подключение к API успешно.")
    return RedirectResponse("/accounts?test=ok", status_code=303)


@app.get("/history", response_class=HTMLResponse)
def history_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "history.html",
        {"request": request, "items": list_payment_history()},
    )


@app.get("/calendar", response_class=HTMLResponse)
def calendar_page(request: Request, year: int = 0, month: int = 0) -> HTMLResponse:
    today = date.today()
    current_year = year or today.year
    current_month = month or today.month
    if current_month < 1 or current_month > 12:
        current_month = today.month
    servers = list_servers()
    by_date: dict[date, list] = defaultdict(list)
    for server in servers:
        by_date[server.next_payment_date].append(server)
    grid = calendar_lib.Calendar(firstweekday=0)
    weeks: list[list[dict[str, object]]] = []
    for week in grid.monthdatescalendar(current_year, current_month):
        cells = []
        for day in week:
            cells.append(
                {
                    "date": day,
                    "in_month": day.month == current_month,
                    "is_today": day == today,
                    "events": by_date.get(day, []),
                }
            )
        weeks.append(cells)
    first_of_month = date(current_year, current_month, 1)
    prev_month = (first_of_month - timedelta(days=1)).replace(day=1)
    next_month = (first_of_month + timedelta(days=31)).replace(day=1)
    month_servers = sorted(
        (
            server
            for server in servers
            if server.next_payment_date.year == current_year
            and server.next_payment_date.month == current_month
        ),
        key=lambda server: server.next_payment_date,
    )
    return templates.TemplateResponse(
        "calendar.html",
        {
            "request": request,
            "weeks": weeks,
            "weekdays": RU_WEEKDAYS,
            "month_title": f"{RU_MONTHS[current_month]} {current_year}",
            "today": today,
            "prev": {"year": prev_month.year, "month": prev_month.month},
            "next": {"year": next_month.year, "month": next_month.month},
            "is_current_month": current_year == today.year and current_month == today.month,
            "month_servers": month_servers,
            "donation_url": DONATION_URL,
        },
    )


def _ics_escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


@app.get("/calendar.ics")
def calendar_ics() -> Response:
    servers = list_servers()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Server Billing Manager//Payments//RU",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Оплаты серверов",
    ]
    for server in servers:
        start = server.next_payment_date.strftime("%Y%m%d")
        end = (server.next_payment_date + timedelta(days=1)).strftime("%Y%m%d")
        amount = ("%g" % server.amount) if server.amount else ""
        summary = f"Оплата: {server.name}"
        if server.provider:
            summary += f" ({server.provider})"
        description_parts = [f"Провайдер: {server.provider}"]
        if amount:
            description_parts.append(f"Сумма: {amount} {server.currency}")
        if server.service_id:
            description_parts.append(f"ID услуги: {server.service_id}")
        description = "\n".join(description_parts)
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:server-{server.id}-{start}@server-billing",
                f"DTSTAMP:{stamp}",
                f"DTSTART;VALUE=DATE:{start}",
                f"DTEND;VALUE=DATE:{end}",
                f"SUMMARY:{_ics_escape(summary)}",
                f"DESCRIPTION:{_ics_escape(description)}",
                "BEGIN:VALARM",
                "TRIGGER:-P1D",
                "ACTION:DISPLAY",
                f"DESCRIPTION:{_ics_escape(summary)}",
                "END:VALARM",
                "END:VEVENT",
            ]
        )
    lines.append("END:VCALENDAR")
    body = "\r\n".join(lines) + "\r\n"
    return Response(
        content=body,
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=server-billing.ics"},
    )


@app.post("/providers/sync")
def sync_providers_catalog() -> RedirectResponse:
    try:
        ok, _message = sync_provider_catalog()
        result = "1" if ok else "0"
    except Exception:
        result = "0"
    return RedirectResponse(f"/providers?catalog={result}", status_code=303)


@app.get("/providers", response_class=HTMLResponse)
def providers_page(request: Request) -> HTMLResponse:
    providers = list_provider_templates()
    return templates.TemplateResponse(
        "providers.html",
        {
            "request": request,
            "providers": providers,
            "countries": provider_countries(providers),
            "catalog": provider_catalog_meta(),
            "donation_url": DONATION_URL,
        },
    )


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, saved: str = "", tested: str = "") -> HTMLResponse:
    current = notification_settings()
    try:
        ssl_threshold = int(get_app_setting("ssl_alert_days", "3") or 3)
    except ValueError:
        ssl_threshold = 3
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "settings": settings,
            "notification": current,
            "ssl_results": run_ssl_checks(),
            "ssl_monitors": list_ssl_monitors(),
            "ssl_threshold": ssl_threshold,
            "ssl_saved": request.query_params.get("ssl_saved", ""),
            "domain_base_url": current.get("base_url", settings.base_url),
            "server_ip": settings.server_ip,
            "current_host": request.url.hostname or "",
            "domain_saved": request.query_params.get("domain_saved", ""),
            "currency": {
                "base": current.get("currency_base", "RUB"),
                "rates": current.get("currency_rates", "RUB:1"),
                "updated_at": current.get("currency_rates_updated_at", ""),
            },
            "token_configured": bool(current.get("telegram_bot_token")),
            "chat_configured": bool(current.get("telegram_chat_id")),
            "saved": saved,
            "tested": tested,
            "bot": request.query_params.get("bot", ""),
            "chat": request.query_params.get("chat", ""),
            "detected_chats": _load_detected_telegram_chats(),
            "telegram_bot_link": telegram_bot_link(str(current.get("telegram_bot_username", ""))),
            "backup_sent": request.query_params.get("backup_sent", ""),
            "checked": request.query_params.get("checked", ""),
            "rates": request.query_params.get("rates", ""),
            "updated": request.query_params.get("updated", ""),
            "update_enabled": bool(settings.app_update_url and settings.app_update_token),
            "catalog_sync": catalog_sync_status(),
            "catalog_sync_enabled": bool(settings.provider_catalog_url),
            "version": current_version(),
            "web_terminal_enabled": web_terminal_enabled(),
        },
    )


@app.post("/settings")
def save_settings(
    reminder_days: str = Form("7,3,1,0,-1"),
    check_interval_seconds: int = Form(86400),
    base_url: str = Form(""),
    backup_interval_days: int = Form(7),
    currency_base: str = Form("RUB"),
    currency_rates: str = Form("RUB:1"),
) -> RedirectResponse:
    save_notification_settings(
        telegram_bot_token=None,
        telegram_chat_id=None,
        reminder_days=reminder_days,
        check_interval_seconds=check_interval_seconds,
        base_url=base_url,
        backup_interval_days=backup_interval_days,
    )
    save_currency_settings(currency_base, currency_rates)
    return RedirectResponse("/settings?saved=1", status_code=303)


@app.post("/settings/telegram/bot")
def save_telegram_bot(telegram_bot_token: str = Form("")) -> RedirectResponse:
    token = telegram_bot_token.strip()
    if not token:
        return RedirectResponse("/settings?bot=0#telegram-setup", status_code=303)
    try:
        username = telegram_bot_username(token)
        ensure_telegram_polling(token)
    except Exception:
        return RedirectResponse("/settings?bot=0#telegram-setup", status_code=303)
    set_app_setting("telegram_bot_token", token)
    set_app_setting("telegram_bot_username", username)
    return RedirectResponse("/settings?bot=1#telegram-setup", status_code=303)


@app.post("/settings/telegram/chat")
def save_telegram_chat(telegram_chat_id: str = Form("")) -> RedirectResponse:
    chat_id = telegram_chat_id.strip()
    if not chat_id:
        return RedirectResponse("/settings?chat=0#telegram-setup", status_code=303)
    set_app_setting("telegram_chat_id", chat_id)
    set_app_setting("telegram_chat_title", "вручную")
    return RedirectResponse("/settings?chat=1#telegram-setup", status_code=303)


@app.post("/settings/telegram/chat/detect")
def detect_telegram_chat() -> RedirectResponse:
    token = notification_settings().get("telegram_bot_token", "").strip()
    if not token:
        return RedirectResponse("/settings?chat=need-bot#telegram-setup", status_code=303)
    try:
        chats = detect_telegram_chats(token)
    except Exception:
        return RedirectResponse("/settings?chat=api#telegram-setup", status_code=303)
    if not chats:
        return RedirectResponse("/settings?chat=0#telegram-setup", status_code=303)
    if len(chats) == 1:
        chat = chats[0]
        set_app_setting("telegram_chat_id", chat["id"])
        set_app_setting("telegram_chat_title", f"{chat['title']} ({chat['type_label']})")
        _clear_detected_telegram_chats()
        return RedirectResponse("/settings?chat=1#telegram-setup", status_code=303)
    _save_detected_telegram_chats(chats)
    return RedirectResponse("/settings?chat=pick#telegram-setup", status_code=303)


@app.post("/settings/telegram/chat/select")
def select_telegram_chat(chat_id: str = Form("")) -> RedirectResponse:
    chat_id = chat_id.strip()
    if not chat_id:
        return RedirectResponse("/settings?chat=0#telegram-setup", status_code=303)
    chats = _load_detected_telegram_chats()
    selected = next((chat for chat in chats if str(chat.get("id")) == chat_id), None)
    if not selected:
        set_app_setting("telegram_chat_id", chat_id)
        set_app_setting("telegram_chat_title", "выбран вручную")
        _clear_detected_telegram_chats()
        return RedirectResponse("/settings?chat=1#telegram-setup", status_code=303)
    set_app_setting("telegram_chat_id", str(selected["id"]))
    title = str(selected.get("title") or "чат")
    type_label = str(selected.get("type_label") or selected.get("type") or "чат")
    set_app_setting("telegram_chat_title", f"{title} ({type_label})")
    _clear_detected_telegram_chats()
    return RedirectResponse("/settings?chat=1#telegram-setup", status_code=303)


@app.post("/settings/currency/refresh")
def refresh_rates() -> RedirectResponse:
    try:
        refresh_currency_rates()
        result = "1"
    except Exception:
        result = "0"
    return RedirectResponse(f"/settings?rates={result}", status_code=303)


@app.post("/settings/telegram/test")
def test_telegram() -> RedirectResponse:
    try:
        sent = send_telegram("Server Billing Manager: тестовое уведомление отправлено.")
        if sent:
            set_app_setting("telegram_tested_at", datetime.now(timezone.utc).isoformat())
    except Exception:
        sent = False
    return RedirectResponse(f"/settings?tested={'1' if sent else '0'}", status_code=303)


@app.post("/settings/telegram/backup")
def send_backup_now() -> RedirectResponse:
    try:
        sent = send_backup()
    except Exception:
        sent = False
    return RedirectResponse(f"/settings?backup_sent={'1' if sent else '0'}", status_code=303)


@app.post("/settings/reminders/run")
def run_reminder_check() -> RedirectResponse:
    try:
        sent = send_due_reminders()
        return RedirectResponse(f"/settings?checked={sent}", status_code=303)
    except Exception:
        return RedirectResponse("/settings?checked=error", status_code=303)


@app.post("/settings/catalog-sync")
def sync_catalog_now() -> RedirectResponse:
    try:
        ok, _message = sync_provider_catalog()
        result = "1" if ok else "0"
    except Exception:
        result = "0"
    return RedirectResponse(f"/settings?catalog={result}#service", status_code=303)


@app.post("/settings/update")
def update_application() -> RedirectResponse:
    try:
        started, _message = start_system_update()
        result = "1" if started else "0"
    except Exception:
        result = "0"
    return RedirectResponse(f"/settings?updated={result}", status_code=303)


@app.post("/settings/web-terminal")
def toggle_web_terminal(enabled: str = Form("0")) -> RedirectResponse:
    set_app_setting("web_terminal_enabled", "1" if enabled.strip() == "1" else "0")
    return RedirectResponse("/settings?saved=1#web-terminal", status_code=303)


@app.post("/settings/password")
def change_password(
    current_password: str = Form(...),
    new_password: str = Form(...),
    new_password_repeat: str = Form(...),
) -> RedirectResponse:
    if not check_login(settings.admin_username, current_password):
        return RedirectResponse("/settings?password=bad-current", status_code=303)
    if len(new_password) < 8 or new_password != new_password_repeat:
        return RedirectResponse("/settings?password=invalid-new", status_code=303)
    set_app_setting("admin_password_hash", hash_password(new_password))
    return RedirectResponse("/settings?password=changed", status_code=303)


@app.get("/ssl")
def ssl_page() -> RedirectResponse:
    return RedirectResponse("/settings#ssl", status_code=303)


@app.post("/ssl/add")
def add_ssl_monitor(host: str = Form(...), port: int = Form(443), label: str = Form("")) -> RedirectResponse:
    if host.strip():
        create_ssl_monitor(host, port, label)
    return RedirectResponse("/settings#ssl", status_code=303)


@app.post("/ssl/{monitor_id}/delete")
def remove_ssl_monitor(monitor_id: int) -> RedirectResponse:
    delete_ssl_monitor(monitor_id)
    return RedirectResponse("/settings#ssl", status_code=303)


@app.post("/ssl/threshold")
def save_ssl_threshold(ssl_alert_days: int = Form(3)) -> RedirectResponse:
    value = ssl_alert_days if ssl_alert_days and ssl_alert_days > 0 else 3
    set_app_setting("ssl_alert_days", str(value))
    return RedirectResponse("/settings?ssl_saved=1#ssl", status_code=303)


@app.get("/analytics", response_class=HTMLResponse)
def analytics_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "analytics.html",
        {
            "request": request,
            "monthly": monthly_expense_summary(),
            "providers": provider_expense_summary(),
        },
    )


@app.get("/domain")
def domain_page() -> RedirectResponse:
    return RedirectResponse("/settings#domain", status_code=303)


@app.post("/domain")
def save_domain(domain: str = Form("")) -> RedirectResponse:
    domain = domain.strip().replace("https://", "").replace("http://", "").strip("/")
    if domain:
        set_app_setting("base_url", f"https://{domain}")
    return RedirectResponse("/settings?domain_saved=1#domain", status_code=303)


@app.get("/servers/{server_id}/terminal", response_class=HTMLResponse)
def terminal_page(request: Request, server_id: int) -> HTMLResponse:
    server = get_server(server_id)
    if server is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        "terminal.html",
        {
            "request": request,
            "server_id": server.id,
            "server_name": server.name,
            "server_host": server.ip_address or "—",
            "ssh_port": server.ssh_port or 22,
            "can_terminal": server.can_terminal,
            "terminal_enabled": web_terminal_enabled(),
            "donation_url": DONATION_URL,
        },
    )


@app.websocket("/servers/{server_id}/terminal/ws")
async def terminal_ws(websocket: WebSocket, server_id: int) -> None:
    await terminal_websocket(websocket, server_id)


@app.get("/servers/{server_id}/pay", response_class=HTMLResponse)
def pay_page(request: Request, server_id: int) -> HTMLResponse:
    server = get_server(server_id)
    if server is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        "pay.html",
        {
            "request": request,
            "server": server,
            "telegram_share_url": build_telegram_share_url(server),
            "history": list_payment_history(server_id),
        },
    )
