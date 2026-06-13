from __future__ import annotations

import hmac
import secrets
import urllib.parse

from fastapi import Request
from starlette.responses import HTMLResponse, Response

CSRF_COOKIE = "sb_csrf"
CSRF_FIELD = "csrf_token"
CSRF_MAX_AGE = 60 * 60 * 24 * 30

_EXEMPT_PATHS = frozenset({"/static"})


def ensure_csrf_token(request: Request) -> str:
    token = request.cookies.get(CSRF_COOKIE, "")
    if len(token) >= 32:
        return token
    return secrets.token_urlsafe(32)


def _is_form_post(request: Request) -> bool:
    content_type = request.headers.get("content-type", "")
    return (
        "application/x-www-form-urlencoded" in content_type
        or "multipart/form-data" in content_type
    )


def _request_is_secure(request: Request) -> bool:
    return (
        request.url.scheme == "https"
        or request.headers.get("x-forwarded-proto", "").lower() == "https"
    )


def attach_csrf_cookie(response: Response, token: str, *, secure: bool) -> None:
    response.set_cookie(
        CSRF_COOKIE,
        token,
        httponly=True,
        secure=secure,
        samesite="strict",
        max_age=CSRF_MAX_AGE,
    )


async def _csrf_form_token(request: Request) -> str:
    """Read csrf_token without request.form() — form() breaks FastAPI Form() params."""
    content_type = request.headers.get("content-type", "")
    if "application/x-www-form-urlencoded" in content_type:
        body = await request.body()
        params = urllib.parse.parse_qs(
            body.decode("utf-8", errors="replace"),
            keep_blank_values=True,
        )
        return (params.get(CSRF_FIELD) or [""])[0]
    if "multipart/form-data" in content_type:
        form = await request.form()
        return str(form.get(CSRF_FIELD, ""))
    return ""


async def csrf_protect_middleware(request: Request, call_next):
    path = request.url.path
    if path.startswith("/static/"):
        return await call_next(request)

    token = ensure_csrf_token(request)
    request.state.csrf_token = token

    if request.method == "POST" and _is_form_post(request):
        form_token = await _csrf_form_token(request)
        cookie_token = request.cookies.get(CSRF_COOKIE, "")
        if (
            not cookie_token
            or not form_token
            or not hmac.compare_digest(str(cookie_token), str(form_token))
        ):
            return HTMLResponse(
                "<h1>403 Forbidden</h1><p>Недействительный CSRF-токен. Обновите страницу и повторите.</p>",
                status_code=403,
            )

    response = await call_next(request)
    secure = _request_is_secure(request)
    if request.cookies.get(CSRF_COOKIE) != token:
        attach_csrf_cookie(response, token, secure=secure)
    return response
