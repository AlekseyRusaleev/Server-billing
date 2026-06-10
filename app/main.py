from __future__ import annotations

import calendar as calendar_lib
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.auth import COOKIE_NAME, check_login, create_session_token, hash_password, is_authenticated
from app.config import settings
from app.db import init_db
from app.repository import (
    create_account,
    create_server,
    delete_account,
    delete_server,
    encrypt_existing_secrets,
    get_account,
    get_server,
    list_payment_history,
    list_accounts,
    list_servers,
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
    update_account,
    update_server,
)
from app.reminders import send_backup, send_due_reminders, send_telegram
from app.connectors import ConnectorError, build_connector
from app.provider_sync import sync_account
from app.provider_templates import list_provider_templates, provider_countries
from app.system_update import start_system_update
from app.telegram import build_telegram_share_url, detect_telegram_chats, telegram_bot_username
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
) -> dict[str, object]:
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


SUPPORTED_INTEGRATIONS = {"manual", "billmanager"}


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
) -> dict[str, object]:
    normalized_integration = integration_type.strip().lower() or "manual"
    if normalized_integration not in SUPPORTED_INTEGRATIONS:
        normalized_integration = "manual"
    return {
        "name": name.strip(),
        "provider": provider.strip(),
        "login": login.strip(),
        "auth_secret": auth_secret.strip(),
        "panel_url": panel_url.strip(),
        "payment_url": payment_url.strip(),
        "notes": notes.strip(),
        "integration_type": normalized_integration,
        "integration_url": integration_url.strip(),
        "auto_sync_enabled": bool(auto_sync_enabled) and normalized_integration != "manual",
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
            "donation_url": DONATION_URL,
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
) -> RedirectResponse:
    update_server(
        server_id,
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
        ),
    )
    return RedirectResponse("/", status_code=303)


@app.post("/servers/{server_id}/paid")
def paid(server_id: int, note: str = Form("")) -> RedirectResponse:
    mark_paid(server_id, note=note)
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
) -> RedirectResponse:
    update_account(
        account_id,
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
        ),
    )
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
    except ConnectorError:
        return RedirectResponse("/accounts?test=error", status_code=303)
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


@app.get("/providers", response_class=HTMLResponse)
def providers_page(request: Request, country: str = "") -> HTMLResponse:
    providers = list_provider_templates()
    selected_country = country.strip().upper()
    filtered_providers = [
        provider
        for provider in providers
        if not selected_country or selected_country in provider.get("countries", [])
    ]
    countries = provider_countries(providers)
    selected_country_name = next(
        (item["name"] for item in countries if item["code"] == selected_country),
        "",
    )
    accounts = list_accounts()
    return templates.TemplateResponse(
        "providers.html",
        {
            "request": request,
            "providers": filtered_providers,
            "all_providers": providers,
            "countries": countries,
            "selected_country": selected_country,
            "selected_country_name": selected_country_name,
            "accounts": accounts,
            "account_options": account_form_options(accounts),
            "provider_templates": providers,
            "donation_url": DONATION_URL,
        },
    )


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, saved: str = "", tested: str = "") -> HTMLResponse:
    current = notification_settings()
    return templates.TemplateResponse(
        "settings.html",
        {
            "request": request,
            "settings": settings,
            "notification": current,
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
            "backup_sent": request.query_params.get("backup_sent", ""),
            "checked": request.query_params.get("checked", ""),
            "rates": request.query_params.get("rates", ""),
            "updated": request.query_params.get("updated", ""),
            "update_enabled": bool(settings.app_update_url and settings.app_update_token),
            "version": current_version(),
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
        chats = []
    if not chats:
        return RedirectResponse("/settings?chat=0#telegram-setup", status_code=303)
    chat = chats[-1]
    set_app_setting("telegram_chat_id", chat["id"])
    set_app_setting("telegram_chat_title", f"{chat['title']} ({chat['type']})")
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


@app.post("/settings/update")
def update_application() -> RedirectResponse:
    try:
        started, _message = start_system_update()
        result = "1" if started else "0"
    except Exception:
        result = "0"
    return RedirectResponse(f"/settings?updated={result}", status_code=303)


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


@app.get("/domain", response_class=HTMLResponse)
def domain_page(request: Request) -> HTMLResponse:
    current = notification_settings()
    host = request.url.hostname or ""
    server_ip = settings.server_ip
    return templates.TemplateResponse(
        "domain.html",
        {
            "request": request,
            "base_url": current.get("base_url", settings.base_url),
            "server_ip": server_ip,
            "current_host": host,
            "saved": request.query_params.get("saved", ""),
        },
    )


@app.post("/domain")
def save_domain(domain: str = Form("")) -> RedirectResponse:
    domain = domain.strip().replace("https://", "").replace("http://", "").strip("/")
    if domain:
        set_app_setting("base_url", f"https://{domain}")
    return RedirectResponse("/domain?saved=1", status_code=303)


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
