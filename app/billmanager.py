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
from app.url_safety import assert_public_http_url

logger = logging.getLogger(__name__)

KNOWN_BILLMANAGER_HOSTS = {
    "qwins.co": "https://my.qwins.co/billmgr",
    "qwins": "https://my.qwins.co/billmgr",
    "onlinevds.ru": "https://my.onlinevds.ru/billmgr",
    "onlinevds": "https://my.onlinevds.ru/billmgr",
    "ln-tech.ru": "https://lk.ln-tech.ru/billmgr",
    "ln-tech": "https://lk.ln-tech.ru/billmgr",
    "firstvds.ru": "https://my.firstvds.ru/billmgr",
    "firstvds": "https://my.firstvds.ru/billmgr",
    "ispserver.ru": "https://my.ispserver.ru/billmgr",
    "ispserver.com": "https://my.ispserver.ru/billmgr",
    "ispserver": "https://my.ispserver.ru/billmgr",
    "ihor-hosting.ru": "https://billing.ihor-hosting.ru/billmgr",
    "ihor-hosting": "https://billing.ihor-hosting.ru/billmgr",
    "hostinux.com": "https://my.hostinux.com/billmgr",
    "hostinux": "https://my.hostinux.com/billmgr",
    "hosting.energy": "https://my.hosting.energy/manager/billmgr",
    "eurobyte.ru": "https://bill.eurobyte.ru/manager/billmgr",
    "eurobyte": "https://bill.eurobyte.ru/manager/billmgr",
    "the.hosting": "https://client.the.hosting/billmgr",
    "itldc.com": "https://my.itldc.com/billmgr",
    "itldc": "https://my.itldc.com/billmgr",
    "zomro.com": "https://cp.zomro.com/billmgr",
    "zomro": "https://cp.zomro.com/billmgr",
    "cp.zomro.com": "https://cp.zomro.com/billmgr",
    "vps.one": "https://bill.vps1.net/billmgr",
    "vps1.net": "https://bill.vps1.net/billmgr",
    "bill.vps1.net": "https://bill.vps1.net/billmgr",
    "host.kg": "https://my.host.kg/manager/billmgr",
}

BILLMANAGER_BUILTIN_PRESETS: tuple[tuple[str, str, str], ...] = (
    ("QWINS", "qwins.co", "https://my.qwins.co/billmgr"),
    ("OnlineVDS", "onlinevds.ru", "https://my.onlinevds.ru/billmgr"),
    ("LN-Tech", "ln-tech.ru", "https://lk.ln-tech.ru/billmgr"),
    ("FirstVDS", "firstvds.ru", "https://my.firstvds.ru/billmgr"),
    ("ISPserver", "ispserver.ru", "https://my.ispserver.ru/billmgr"),
    ("iHor Hosting", "ihor-hosting.ru", "https://billing.ihor-hosting.ru/billmgr"),
    ("Hostinux", "hostinux.com", "https://my.hostinux.com/billmgr"),
    ("Hosting.Energy", "hosting.energy", "https://my.hosting.energy/manager/billmgr"),
    ("Eurobyte", "eurobyte.ru", "https://bill.eurobyte.ru/manager/billmgr"),
    ("THE.Hosting", "the.hosting", "https://client.the.hosting/billmgr"),
    ("ITLDC", "itldc.com", "https://my.itldc.com/billmgr"),
    ("Zomro", "zomro.com", "https://cp.zomro.com/billmgr"),
    ("VPS.one", "vps.one", "https://bill.vps1.net/billmgr"),
    ("Host.kg", "host.kg", "https://my.host.kg/manager/billmgr"),
)

# Функции списка услуг по типам продуктов. Работают и в BM5, и в BM6.
SERVICE_FUNCTIONS = ("vds", "dedic", "vhost")

# Кандидаты имён полей в <elem> — берём первое непустое.
NAME_FIELDS = ("domain", "serverid", "name", "desc", "fullname")
IP_FIELDS = ("ip", "ipaddr", "ip_addr", "addr")
EXPIRE_FIELDS = ("expiredate", "real_expiredate", "expire")
COST_AMOUNT_FIELDS = ("cost_iso", "paymethodamount_iso", "cost", "price")
CURRENCY_FIELDS = ("currency", "currency_iso", "costcurrency")
LOCATION_FIELDS = ("datacentername", "location", "datacenter")
PERIOD_FIELDS = ("costperiod", "period", "periodday", "billperiod")

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


def billmanager_presets(templates: list[dict[str, object]] | None = None) -> list[dict[str, str]]:
    """Известные URL BILLmanager для выпадающих списков в форме аккаунта."""
    seen: set[str] = set()
    presets: list[dict[str, str]] = []

    def add(
        name: str,
        domain: str,
        integration_url: str,
        panel_url: str = "",
        payment_url: str = "",
    ) -> None:
        url = normalize_billmgr_url(integration_url)
        if not url or url in seen:
            return
        seen.add(url)
        cabinet = (panel_url or "").strip() or f"{url}?func=logon"
        pay = (payment_url or "").strip() or cabinet
        presets.append(
            {
                "name": name.strip() or domain.strip() or url,
                "domain": domain.strip(),
                "integration_url": url,
                "panel_url": cabinet,
                "payment_url": pay,
            }
        )

    for item in templates or []:
        if str(item.get("integration_type") or "") != "billmanager":
            continue
        add(
            str(item.get("name") or ""),
            str(item.get("domain") or ""),
            str(item.get("integration_url") or ""),
            str(item.get("panel_url") or ""),
            str(item.get("payment_url") or ""),
        )

    for label, domain, url in BILLMANAGER_BUILTIN_PRESETS:
        add(label, domain, url)

    presets.sort(key=lambda item: item["name"].lower())
    return presets


def integration_host_options(templates: list[dict[str, object]] | None = None) -> list[dict[str, str]]:
    """Хостеры с готовой интеграцией для выпадающих списков в формах."""
    options: list[dict[str, str]] = []
    seen: set[str] = set()

    def add_option(
        name: str,
        domain: str,
        integration_type: str,
        integration_url: str = "",
        panel_url: str = "",
        payment_url: str = "",
        service_hint: str = "",
        notes: str = "",
    ) -> None:
        key = (domain or name).strip().lower()
        if not key or key in seen:
            return
        seen.add(key)
        options.append(
            {
                "name": name.strip(),
                "domain": domain.strip() or name.strip(),
                "integration_type": integration_type,
                "integration_url": integration_url,
                "panel_url": panel_url,
                "payment_url": payment_url,
                "service_hint": service_hint,
                "notes": notes,
            }
        )

    for preset in billmanager_presets(templates):
        add_option(
            preset["name"],
            preset["domain"],
            "billmanager",
            preset["integration_url"],
            preset["panel_url"],
            preset["payment_url"],
        )

    for item in templates or []:
        integration = str(item.get("integration_type") or "manual")
        if integration in ("manual", "billmanager"):
            continue
        add_option(
            str(item.get("name") or integration),
            str(item.get("domain") or ""),
            integration,
            str(item.get("integration_url") or ""),
            str(item.get("panel_url") or ""),
            str(item.get("payment_url") or ""),
            str(item.get("service_hint") or ""),
            str(item.get("notes") or ""),
        )

    options.sort(key=lambda item: item["name"].lower())
    return options


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


def billmanager_cabinet_url(integration_url: str, panel_url: str, provider: str = "") -> str:
    panel = (panel_url or "").strip()
    if panel:
        return panel
    billmgr = resolve_billmanager_url(integration_url, panel_url, provider)
    return f"{billmgr}?func=logon" if billmgr else ""


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
        self._session_id: str | None = None
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
        assert_public_http_url(self.endpoint, context="BILLmanager URL")
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
            error_type = (error_node.get("type") or "").strip()
            obj = (error_node.get("object") or "").strip()
            message = (error_node.findtext("msg") or error_type or "ошибка").strip()
            detail = " ".join(part for part in (error_type, obj, message) if part)
            raise ConnectorError(f"BILLmanager: {detail}")
        return root

    def _auth_params(self) -> dict[str, str]:
        if self._session_id:
            return {"auth": self._session_id}
        return {"authinfo": f"{self.login}:{self.password}"}

    def _establish_session(self) -> None:
        if self._session_id:
            return
        params = {
            "func": "auth",
            "username": self.login,
            "password": self.password,
            "out": "xml",
        }
        last_error: ConnectorError | None = None
        for method in ("POST", "GET"):
            try:
                root = self._parse_xml(self._fetch_raw(params, method))
                auth = root.find("auth")
                session = ""
                if auth is not None:
                    session = (auth.text or auth.get("id") or "").strip()
                if not session:
                    raise ConnectorError("BILLmanager: пустой ответ сессии API.")
                self._session_id = session
                return
            except ConnectorError as error:
                last_error = error
        if last_error is not None:
            raise last_error
        raise ConnectorError("BILLmanager: не удалось авторизоваться через сессию.")

    @staticmethod
    def _should_use_session(error: ConnectorError) -> bool:
        text = str(error).lower()
        return any(
            phrase in text
            for phrase in (
                "forbidden_auth",
                "authorization error",
                "insufficient privileges",
                "badpassword",
                "invalid username or password",
            )
        )

    def _request(
        self,
        func: str,
        extra: dict[str, str] | None = None,
        *,
        _session_retry: bool = False,
    ) -> ET.Element:
        params = {
            **self._auth_params(),
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
        if last_error is None:
            raise ConnectorError("BILLmanager: ошибка запроса.")
        if not _session_retry and not self._session_id and self._should_use_session(last_error):
            self._establish_session()
            return self._request(func, extra, _session_retry=True)
        raise last_error

    @staticmethod
    def _is_auth_error(error: ConnectorError) -> bool:
        text = str(error).lower()
        return any(
            word in text
            for word in (
                "auth",
                "access",
                "доступ",
                "логин",
                "парол",
                "forbidden",
                "privileges",
                "permission",
                "badpassword",
            )
        )

    def test_connection(self) -> None:
        last_error: ConnectorError | None = None
        for func in ("vds", "whoami", "usrparam"):
            try:
                self._request(func)
                return
            except ConnectorError as error:
                if self._is_auth_error(error):
                    raise ConnectorError(
                        "BILLmanager: неверный логин или пароль, либо API недоступен с этого сервера."
                    ) from error
                last_error = error
        if last_error is not None:
            raise last_error

    def list_services(self) -> list[RemoteService]:
        services: list[RemoteService] = []
        seen: set[str] = set()
        any_success = False
        for func in SERVICE_FUNCTIONS:
            root = None
            for extra in ({"filter": "on"}, None):
                try:
                    root = self._request(func, extra)
                    break
                except ConnectorError as error:
                    if self._is_auth_error(error):
                        raise ConnectorError(
                            "BILLmanager: неверный логин или пароль, либо API недоступен с этого сервера."
                        ) from error
                    if extra is not None:
                        logger.info("BILLmanager func=%s filter=on недоступна: %s", func, error)
                        continue
                    logger.info("BILLmanager func=%s недоступна: %s", func, error)
            if root is None:
                continue
            any_success = True
            for elem in root.findall("elem"):
                service = _parse_service(elem)
                if service is None or service.service_id in seen:
                    continue
                seen.add(service.service_id)
                services.append(service)
        if not any_success:
            raise ConnectorError(
                "BILLmanager не отдал ни одного списка услуг (vds/dedic/vhost). "
                "Проверьте логин/пароль от кабинета и доступ API у хостера."
            )
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


def _parse_currency(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    lower = text.lower()
    if any(marker in lower for marker in ("$", "usd", "us$")):
        return "USD"
    if any(marker in lower for marker in ("€", "eur")):
        return "EUR"
    if any(marker in text for marker in ("₽",)) or any(marker in lower for marker in ("rub", "руб")):
        return "RUB"
    parts = text.split()
    if parts:
        tail = parts[-1].upper()
        if len(tail) == 3 and tail.isalpha():
            return tail
    return ""


def _parse_billing_period(raw: str) -> int | None:
    text = (raw or "").strip().lower()
    if not text:
        return None
    if any(word in text for word in ("year", "год", "лет", "annual")):
        return 365
    if any(word in text for word in ("month", "мес", "mes")):
        return 30
    if any(word in text for word in ("week", "нед")):
        return 7
    days = _parse_cost(text)
    if days is not None and days >= 7:
        return int(days)
    return None


def _parse_money(elem: ET.Element) -> tuple[float | None, str]:
    for field in COST_AMOUNT_FIELDS:
        raw = elem.findtext(field) or ""
        if not raw.strip():
            continue
        amount = _parse_cost(raw)
        if amount is None:
            continue
        currency = _parse_currency(raw)
        if not currency:
            currency = _first_text(elem, CURRENCY_FIELDS)
        return amount, currency.upper() if currency else ""
    return None, ""


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
    amount, currency = _parse_money(elem)
    billing_period = _parse_billing_period(_first_text(elem, PERIOD_FIELDS))
    display_name = _first_text(elem, NAME_FIELDS) or service_id
    pricelist = (elem.findtext("pricelist") or "").strip()
    if pricelist and display_name and pricelist not in display_name:
        display_name = f"{pricelist} · {display_name}"
    return RemoteService(
        service_id=service_id,
        name=display_name,
        ip_address=_first_text(elem, IP_FIELDS),
        status=STATUS_MAP.get(status_raw, "active"),
        next_payment_date=_parse_date(_first_text(elem, EXPIRE_FIELDS)),
        amount=amount,
        currency=currency,
        billing_period_days=billing_period,
        location=_first_text(elem, LOCATION_FIELDS),
    )
