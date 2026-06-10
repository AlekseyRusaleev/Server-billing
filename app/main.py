from __future__ import annotations

from datetime import date

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
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
from app.system_update import start_system_update
from app.telegram import build_telegram_share_url

app = FastAPI(title=settings.app_name)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

SUPPORTED_CURRENCIES = {"RUB", "USD", "USDT"}


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
    service_id: str,
    amount: float,
    currency: str,
    billing_period_days: int,
    next_payment_date: str,
    payment_url: str,
    panel_url: str,
    notes: str,
) -> dict[str, object]:
    normalized_currency = currency.strip().upper() or "RUB"
    if normalized_currency not in SUPPORTED_CURRENCIES:
        normalized_currency = "RUB"
    return {
        "hosting_account_id": hosting_account_id or None,
        "name": name.strip(),
        "provider": provider.strip(),
        "ip_address": ip_address.strip(),
        "service_id": service_id.strip(),
        "amount": amount,
        "currency": normalized_currency,
        "billing_period_days": billing_period_days,
        "next_payment_date": next_payment_date,
        "payment_url": payment_url.strip(),
        "panel_url": panel_url.strip(),
        "notes": notes.strip(),
    }


def account_payload(
    name: str,
    provider: str,
    login: str,
    auth_secret: str,
    panel_url: str,
    payment_url: str,
    notes: str,
) -> dict[str, object]:
    return {
        "name": name.strip(),
        "provider": provider.strip(),
        "login": login.strip(),
        "auth_secret": auth_secret.strip(),
        "panel_url": panel_url.strip(),
        "payment_url": payment_url.strip(),
        "notes": notes.strip(),
    }


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
    onboarding = [
        {"label": "Создать аккаунт хостинга", "done": bool(accounts), "href": "/accounts"},
        {"label": "Добавить первый сервер", "done": bool(all_servers), "href": "/"},
        {
            "label": "Настроить Telegram",
            "done": bool(current_notifications.get("telegram_bot_token") and current_notifications.get("telegram_chat_id")),
            "href": "/settings",
        },
        {"label": "Проверить backup", "done": bool(current_notifications.get("backup_interval_days")), "href": "/settings"},
        {"label": "Проверить адрес сервиса", "done": bool(current_notifications.get("base_url")), "href": "/domain"},
    ]
    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "servers": servers,
            "accounts": accounts,
            "providers": providers,
            "filters": {"q": q, "provider": provider, "state": state},
            "monthly_plan": monthly_plan,
            "onboarding": onboarding,
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
    service_id: str = Form(""),
    amount: float = Form(0),
    currency: str = Form("RUB"),
    billing_period_days: int = Form(30),
    next_payment_date: str = Form(...),
    payment_url: str = Form(""),
    panel_url: str = Form(""),
    notes: str = Form(""),
) -> RedirectResponse:
    create_server(
        form_payload(
            hosting_account_id,
            name,
            provider,
            ip_address,
            service_id,
            amount,
            currency,
            billing_period_days,
            next_payment_date,
            payment_url,
            panel_url,
            notes,
        )
    )
    return RedirectResponse("/", status_code=303)


@app.get("/servers/{server_id}/edit", response_class=HTMLResponse)
def edit_server(request: Request, server_id: int) -> HTMLResponse:
    server = get_server(server_id)
    if server is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        "edit.html",
        {"request": request, "server": server, "accounts": list_accounts()},
    )


@app.post("/servers/{server_id}/edit")
def save_server(
    server_id: int,
    hosting_account_id: int = Form(0),
    name: str = Form(...),
    provider: str = Form(...),
    ip_address: str = Form(""),
    service_id: str = Form(""),
    amount: float = Form(0),
    currency: str = Form("RUB"),
    billing_period_days: int = Form(30),
    next_payment_date: str = Form(...),
    payment_url: str = Form(""),
    panel_url: str = Form(""),
    notes: str = Form(""),
) -> RedirectResponse:
    update_server(
        server_id,
        form_payload(
            hosting_account_id,
            name,
            provider,
            ip_address,
            service_id,
            amount,
            currency,
            billing_period_days,
            next_payment_date,
            payment_url,
            panel_url,
            notes,
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
        {"request": request, "accounts": list_accounts()},
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
) -> RedirectResponse:
    create_account(account_payload(name, provider, login, auth_secret, panel_url, payment_url, notes))
    return RedirectResponse("/accounts", status_code=303)


@app.get("/accounts/{account_id}/edit", response_class=HTMLResponse)
def edit_account(request: Request, account_id: int) -> HTMLResponse:
    account = get_account(account_id)
    if account is None:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        "account_edit.html",
        {"request": request, "account": account},
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
) -> RedirectResponse:
    update_account(
        account_id,
        account_payload(name, provider, login, auth_secret, panel_url, payment_url, notes),
    )
    return RedirectResponse("/accounts", status_code=303)


@app.post("/accounts/{account_id}/delete")
def remove_account(account_id: int) -> RedirectResponse:
    delete_account(account_id)
    return RedirectResponse("/accounts", status_code=303)


@app.get("/history", response_class=HTMLResponse)
def history_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        "history.html",
        {"request": request, "items": list_payment_history()},
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
            "saved": saved,
            "tested": tested,
            "backup_sent": request.query_params.get("backup_sent", ""),
            "checked": request.query_params.get("checked", ""),
            "rates": request.query_params.get("rates", ""),
            "updated": request.query_params.get("updated", ""),
            "update_enabled": bool(settings.app_update_url and settings.app_update_token),
        },
    )


@app.post("/settings")
def save_settings(
    telegram_bot_token: str = Form(""),
    telegram_chat_id: str = Form(""),
    reminder_days: str = Form("7,3,1,0,-1"),
    check_interval_seconds: int = Form(86400),
    base_url: str = Form(""),
    backup_interval_days: int = Form(7),
    currency_base: str = Form("RUB"),
    currency_rates: str = Form("RUB:1"),
) -> RedirectResponse:
    save_notification_settings(
        telegram_bot_token=telegram_bot_token,
        telegram_chat_id=telegram_chat_id,
        reminder_days=reminder_days,
        check_interval_seconds=check_interval_seconds,
        base_url=base_url,
        backup_interval_days=backup_interval_days,
    )
    save_currency_settings(currency_base, currency_rates)
    return RedirectResponse("/settings?saved=1", status_code=303)


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
