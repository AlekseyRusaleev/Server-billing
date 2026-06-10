# Server Billing Manager

Self-hosted панель для учета серверов, сроков оплаты и быстрых переходов к оплате у хостинг-провайдера.

Сервис не проводит платежи сам и не хранит банковские карты. Он напоминает о сроках, показывает нужный сервер, хранит ссылку на кабинет/оплату и помогает быстро перейти к провайдеру.

## Возможности

- Дашборд серверов, провайдеров и ближайших оплат.
- Аккаунты хостинга: провайдер, логин, секрет, ссылка на кабинет, ссылка на оплату.
- Привязка нескольких серверов к одному аккаунту хостинга.
- Статусы оплаты: в порядке, скоро, срочно, просрочено.
- Страница оплаты конкретного сервера.
- Кнопка перехода на ссылку оплаты сервера или аккаунта хостинга.
- Ручная кнопка `Отметить оплачено`, которая переносит дату на следующий период.
- Telegram-уведомления через отдельный reminder worker.
- Развертывание через Docker Compose и Caddy.
- Работа по IP из коробки, опционально домен с HTTPS через Caddy.

## Установка одной командой

На чистом Linux-сервере выполните:

```bash
curl -fsSL https://raw.githubusercontent.com/AlekseyRusaleev/Server-billing/main/scripts/install.sh | sudo bash
```

Установщик спросит:

- домен, если хотите HTTPS;
- email для сертификатов Caddy, если указан домен;
- Telegram bot token, если нужны уведомления;
- Telegram chat id, если указан токен.

Если домен не указан, установщик автоматически создаст HTTPS-адрес через `sslip.io`:

```text
https://YOUR_SERVER_IP.sslip.io
```

Обычный адрес `http://YOUR_SERVER_IP` будет редиректить на защищенную HTTPS-ссылку.

## HTTPS Без Домена

Свой домен не обязателен. По умолчанию используется публичный DNS-алиас `sslip.io`, который указывает на IP сервера:

```text
192.0.2.10.sslip.io -> 192.0.2.10
```

Caddy автоматически выпускает сертификат Let's Encrypt для этого имени.

## Установка с доменом

1. Создайте DNS `A`-запись домена на IP сервера.
2. Запустите install script.
3. Введите домен, например:

```text
billing.example.com
```

Caddy автоматически получит и обновит HTTPS-сертификат.

## Локальный запуск для разработки

Windows PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Откройте:

```text
http://127.0.0.1:8000
```

## Docker локально

```bash
docker compose up --build
```

После запуска:

```text
http://127.0.0.1:8000
```

## Production Docker Compose

Production-режим использует:

- `app` - FastAPI web app;
- `scheduler` - Telegram reminder worker;
- `caddy` - reverse proxy, IP access и HTTPS для домена.

Ручной запуск:

```bash
cp .env.example .env
docker compose -f docker-compose.prod.yml up -d --build
```

## Настройки `.env`

```env
APP_NAME=Server Billing Manager
DATABASE_PATH=/app/data/server_billing.db
BASE_URL=https://YOUR_SERVER_IP.sslip.io
SERVER_IP=YOUR_SERVER_IP
CADDY_SITE_ADDRESS=YOUR_SERVER_IP.sslip.io
CADDY_EMAIL=
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
REMINDER_DAYS=7,3,1,0,-1
CHECK_INTERVAL_SECONDS=86400
```

Для домена:

```env
BASE_URL=https://billing.example.com
CADDY_SITE_ADDRESS=billing.example.com
CADDY_EMAIL=admin@example.com
```

## Telegram

Создайте бота через `@BotFather`, затем получите `chat_id` пользователя или группы, куда нужно отправлять уведомления.

Токен не хранится в репозитории. При установке он вводится интерактивно и записывается только в `.env` на вашем сервере.

Если `TELEGRAM_BOT_TOKEN` или `TELEGRAM_CHAT_ID` пустые, reminder worker работает, но уведомления не отправляет.

## Обновление

```bash
cd /opt/server-billing
git pull
docker compose -f docker-compose.prod.yml up -d --build
```

## Данные

SQLite-база хранится в:

```text
/opt/server-billing/data/server_billing.db
```

Перед обновлениями или переносом сервера сделайте резервную копию папки `data`.

## Безопасность

Текущая версия - self-hosted MVP. Встроенной авторизации пользователей пока нет: доступ открывается по IP или домену, который вы укажете при установке.

Пароль/секрет аккаунта хостинга хранится в SQLite. Для коммерческой multi-tenant версии нужно добавить шифрование секретов, пользователей, роли и аудит доступа.
