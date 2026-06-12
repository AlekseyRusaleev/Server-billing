"""Коннектор к BILLmanager (ISPsystem).

Только чтение: получает список услуг клиента и их параметры. Не создаёт счета и
не заказывает услуги. API у разных провайдеров и версий (BILLmanager 5 / 6)
отличается именами функций и полей, поэтому парсинг намеренно защитный —
читаем первое подходящее поле и не падаем на отсутствующих.

Док.: https://www.ispsystem.ru/docs/billmanager/razrabotchiku/billmanager-api
"""
from __future__ import annotations

import logging
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from urllib.parse import urlparse, urlunparse

from app.connectors import ConnectorError, RemoteService

logger = logging.getLogger(__name__)

KNOWN_BILLMANAGER_HOSTS = {
    "qwins.co": "https://my.qwins.co/billmgr",
    "qwins": "https://my.qwins.co/billmgr",
    "onlinevds.ru": "https://my.onlinevds.ru/billmgr",
    "onlinevds": "https://my.onlinevds.ru/billmgr",
    "ln-tech.ru": "https://lk.ln-tech.ru/billmgr",
    "ln-tech": "https://lk.ln-tech.ru/billmgr",
}

# Функции списка услуг по типам продуктов. Работают и в BM5, и в BM6.
SERVICE_FUNCTIONS = ("vds", "dedic", "vhost")

# Кандидаты имён полей в <elem> — берём первое непустое.
NAME_FIELDS = ("name", "domain", "desc", "fullname")
IP_FIELDS = ("ip", "ipaddr", "ip_addr", "addr")
EXPIRE_FIELDS = ("real_expiredate", "expiredate", "expire", "paydate")
COST_FIELDS = ("cost", "costperiod", "price", "cost_iso", "paymethodamount_iso")

# Коды статусов BILLmanager -> внутренние статусы панели.
STATUS_MAP = {
    "1": "active",   # заказана
    "2": "active",   # активна
    "3": "suspended",  # приостановлена
    "4": "suspended",  # приостановлена администратором
    "5": "deleted",  # удалена
}


def normalize_billmgr_url(raw: str) -> str:
    """Приводит URL к https://host/billmgr без ?func=logon и лишних сегментов."""
    text = (raw or "").strip()
    if not text:
        return ""
    if not text.startswith(("http://", "https://")):
        text = f"https://{text.lstrip('/')}"
    parsed = urlparse(text)
    if not parsed.netloc:
        return ""
    path = parsed.path or ""
    lower_path = path.lower()
    if "/billmgr" in lower_path:
        idx = lower_path.index("/billmgr")
        path = path[: idx + len("/billmgr")]
    else:
        path = (path.rstrip("/") or "") + "/billmgr"
    scheme = parsed.scheme or "https"
    return urlunparse((scheme, parsed.netloc, path, "", "", ""))


def resolve_billmanager_url(integration_url: str, panel_url: str, provider: str = "") -> str:
    for candidate in (integration_url, panel_url):
        normalized = normalize_billmgr_url(candidate)
        if normalized:
            return normalized
    hint = (provider or "").strip().lower()
    if not hint:
        return ""
    for key, url in KNOWN_BILLMANAGER_HOSTS.items():
        if key in hint:
            return url
    return ""


class BillmanagerConnector:
    def __init__(
        self,
        login: str,
        password: str,
        timeout: int = 25,
        *,
        integration_url: str = "",
        panel_url: str = "",
        provider: str = "",
    ) -> None:
        self.base_url = resolve_billmanager_url(integration_url, panel_url, provider)
        self.login = (login or "").strip()
        self.password = password or ""
        self.timeout = timeout
        if not self.base_url:
            raise ConnectorError(
                "Не указан URL BILLmanager. Для QWINS: https://my.qwins.co/billmgr"
            )
        if not self.login or not self.password:
            raise ConnectorError("Не указаны логин или пароль от кабинета BILLmanager.")

    @property
    def endpoint(self) -> str:
        return self.base_url

    def _fetch_raw(self, params: dict[str, str], method: str) -> bytes:
        headers = {"User-Agent": "server-billing-manager/1.0"}
        if method == "POST":
            body = urllib.parse.urlencode(params).encode("utf-8")
            request = urllib.request.Request(
                self.endpoint,
                data=body,
                method="POST",
                headers=headers,
            )
        else:
            url = f"{self.endpoint}?{urllib.parse.urlencode(params)}"
            request = urllib.request.Request(url, method="GET", headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            detail = error.read()[:200].decode("utf-8", errors="ignore").strip()
            suffix = f" ({detail})" if detail else ""
            raise ConnectorError(f"BILLmanager вернул HTTP {error.code}{suffix}.") from error
        except urllib.error.URLError as error:
            raise ConnectorError(f"Не удалось подключиться к BILLmanager: {error.reason}.") from error

    def _parse_xml(self, raw: bytes) -> ET.Element:
        text = raw.decode("utf-8", errors="ignore").strip()
        if not text.startswith("<"):
            snippet = text[:160].replace("\n", " ")
            raise ConnectorError(f"BILLmanager вернул не XML: {snippet or 'пустой ответ'}")
        try:
            root = ET.fromstring(raw)
        except ET.ParseError as error:
            raise ConnectorError("BILLmanager вернул неожиданный ответ (не XML).") from error
        error_node = root.find("error")
        if error_node is not None:
            message = (error_node.findtext("msg") or error_node.get("type") or "ошибка").strip()
            raise ConnectorError(f"BILLmanager: {message}")
        return root

    def _request(self, func: str, extra: dict[str, str] | None = None) -> ET.Element:
        params = {
            "authinfo": f"{self.login}:{self.password}",
            "func": func,
            "out": "xml",
        }
        if extra:
            params.update(extra)
        last_error: ConnectorError | None = None
        for method in ("POST", "GET"):
            try:
                return self._parse_xml(self._fetch_raw(params, method))
            except ConnectorError as error:
                last_error = error
        if last_error is not None:
            raise last_error
        raise ConnectorError("BILLmanager: ошибка запроса.")

    @staticmethod
    def _is_auth_error(error: ConnectorError) -> bool:
        text = str(error).lower()
        return any(word in text for word in ("auth", "access", "доступ", "логин", "парол", "forbidden"))

    def test_connection(self) -> None:
        last_error: ConnectorError | None = None
        for func in ("vds", "whoami", "usrparam"):
            try:
                self._request(func)
                return
            except ConnectorError as error:
                if self._is_auth_error(error):
                    raise
                last_error = error
        if last_error is not None:
            raise last_error

    def list_services(self) -> list[RemoteService]:
        services: list[RemoteService] = []
        seen: set[str] = set()
        any_success = False
        for func in SERVICE_FUNCTIONS:
            try:
                root = self._request(func, {"filter": "on"})
            except ConnectorError as error:
                if self._is_auth_error(error):
                    raise
                logger.info("BILLmanager func=%s недоступна: %s", func, error)
                continue
            any_success = True
            for elem in root.findall("elem"):
                service = _parse_service(elem)
                if service is None or service.service_id in seen:
                    continue
                seen.add(service.service_id)
                services.append(service)
        if not any_success:
            raise ConnectorError("BILLmanager не отдал ни одного списка услуг (vds/dedic/vhost).")
        return services


def _first_text(elem: ET.Element, fields: tuple[str, ...]) -> str:
    for field in fields:
        value = elem.findtext(field)
        if value and value.strip():
            return value.strip()
    return ""


def _parse_cost(raw: str) -> float | None:
    if not raw:
        return None
    cleaned = raw.strip().replace("\u00a0", "").replace(" ", "")
    number = ""
    for char in cleaned:
        if char.isdigit() or char in ".,":
            number += char
        elif number:
            break
    if not number:
        return None
    if "," in number and "." in number:
        number = number.replace(",", "")  # запятая = разделитель тысяч
    elif "," in number:
        number = number.replace(",", ".")  # запятая = десятичный разделитель
    try:
        return float(number)
    except ValueError:
        return None


def _parse_date(raw: str):
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(raw.strip()[: len(fmt) + 2], fmt).date()
        except ValueError:
            continue
    try:
        return datetime.strptime(raw.strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _parse_service(elem: ET.Element) -> RemoteService | None:
    service_id = (elem.findtext("id") or elem.get("id") or "").strip()
    if not service_id:
        return None
    status_raw = (elem.findtext("status") or "").strip()
    return RemoteService(
        service_id=service_id,
        name=_first_text(elem, NAME_FIELDS) or service_id,
        ip_address=_first_text(elem, IP_FIELDS),
        status=STATUS_MAP.get(status_raw, "active"),
        next_payment_date=_parse_date(_first_text(elem, EXPIRE_FIELDS)),
        amount=_parse_cost(_first_text(elem, COST_FIELDS)),
        currency="",
    )
