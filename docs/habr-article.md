# Статья для Habr (черновик — опубликуйте от своего аккаунта)

**Заголовок:** Self-hosted панель для учёта VPS: сроки оплаты, Telegram и sync провайдеров за одну команду

**Теги:** Open Source, VPS, Docker, Python, DevOps, self-hosted

---

## Лид

Если у вас больше пяти VPS у разных хостеров, Excel и закладки перестают работать: забываешь продление, теряешь ссылку на биллинг, не видишь суммарную нагрузку по месяцам. Я собрал **Server Billing Manager** — self-hosted панель на FastAPI с установкой одной командой, Telegram-напоминаниями и read-only синхронизацией с API провайдеров.

Репозиторий: https://github.com/AlekseyRusaleev/Server-billing

---

## Проблема

- Даты оплаты размазаны по письмам и личным кабинетам.
- У каждого провайдера свой URL оплаты и свой формат данных.
- Notion/таблица не шлёт напоминания и не синхронизируется с API.
- Хочется держать данные у себя, а не в чужом SaaS.

---

## Что делает панель

**Не платёжный шлюз** — карты не хранит, деньги не проводит. Это дашборд:

- серверы, IP, статусы оплаты (в порядке / скоро / просрочено);
- ссылки «перейти к оплате» и «отметить оплачено» с переносом даты;
- аккаунты хостинга (несколько серверов на один логин провайдера);
- история платежей, аналитика, календарь с экспортом в iCal;
- каталог ~36 провайдеров с фильтрами и ориентирами по ценам;
- Telegram: напоминания, сводки после autosync, зашифрованный backup SQLite.

![Dashboard](https://github.com/user-attachments/assets/3b7ca0f4-1c3d-4087-9002-416d0066f5fd)

---

## Установка

На чистом Linux VPS:

```bash
curl -fsSL https://raw.githubusercontent.com/AlekseyRusaleev/Server-billing/main/scripts/install.sh | bash
```

Install спрашивает домен (или выдаёт `https://IP.sslip.io`), логин/пароль админа и **пароль разблокировки ключей шифрования** — его нужно сохранить отдельно.

Стек: Docker Compose + Caddy (HTTPS из коробки) или nginx, если 443 уже занят.

---

## Интеграции с провайдерами

1. **Ручной режим** — ссылки и даты вводите сами.
2. **BILLmanager** — совместимые API: услуги, IP, суммы, даты продления (только чтение).
3. **Web API** — REST провайдеров по API-ключу; в каталоге отмечены шаблоны с badge «API sync».

Autosync по расписанию + флаг «не перезаписывать при синхронизации» на конкретном сервере.

---

## Безопасность (кратко)

- Один админ, CSRF, rate limit на login, IP allowlist.
- Пароли SSH и API-ключи в SQLite — Fernet.
- Master-ключ не лежит на диске открытым текстом: `encryption.key.wrap` + passphrase ([SECURITY.md](https://github.com/AlekseyRusaleev/Server-billing/blob/main/SECURITY.md)).
- Backup в Telegram — файл `.db.enc`, не сырой дамп.

Для личного VPS это баланс между «секреты в .env» и «поднимать HashiCorp Vault за $1000+/мес».

---

## Кому подойдёт

- Админам с 5–50 VPS у разных hoster'ов.
- Homelab / freelance infra.
- Тем, кто хочет self-hosted без подписки.

Не подойдёт как multi-tenant SaaS для клиентов (одна учётка админа).

---

## Планы и участие

Проект под **MIT**, PR с новыми провайдерами приветствуются: [CONTRIBUTING.md](https://github.com/AlekseyRusaleev/Server-billing/blob/main/CONTRIBUTING.md).

Если полезно — star в репозитории и feedback через Issues (best-effort).

---

## P.S.

Поддержать разработку можно через Telegram Stars: @AlekseyRdonate_bot
