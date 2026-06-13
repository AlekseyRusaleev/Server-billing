from __future__ import annotations

import hmac
import secrets

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


async def csrf_protect_middleware(request: Request, call_next):
    path = request.url.path
    if path.startswith("/static/"):
        return await call_next(request)

    token = ensure_csrf_token(request)
    request.state.csrf_token = token

    if request.method == "POST" and _is_form_post(request):
        form = await request.form()
        cookie_token = request.cookies.get(CSRF_COOKIE, "")
        form_token = form.get(CSRF_FIELD, "")
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
