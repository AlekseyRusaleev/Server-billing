# Модель угроз и хранение секретов

Документ для администраторов, которые разворачивают **Server Billing Manager** у себя или распространяют панель другим.

## Что защищаем

| Данные | Где лежат | Как защищены |
|--------|-----------|--------------|
| Пароль администратора | `.env` (`ADMIN_PASSWORD_HASH`) | PBKDF2-SHA256 |
| Сессии (cookie) | Подпись HMAC | `secrets/session.key` |
| SSH-пароли, API-ключи провайдеров, token Telegram | SQLite (`data/`) | Fernet, master-ключ из `secrets/` |
| Резервные копии в Telegram | Файл `.db.enc` | Тот же Fernet-ключ |

**Цель:** утечка только `data/` (бэкап БД, snapshot диска без `secrets/`) **не должна** раскрывать пароли и ключи пользователей.

## Схема ключей (рекомендуемая)

```
secrets/
  session.key              — ключ подписи сессий (случайный, на диске)
  encryption.key.wrap      — master-ключ шифрования БД, обёрнутый паролем (PBKDF2 + Fernet)
  unlock.passphrase        — пароль разблокировки (мин. 12 символов; только для старта контейнера)
```

Сырой `encryption.key` **не хранится** на диске после установки или `wrap-encryption-key.sh`. Master-ключ существует в памати процесса после успешной разблокировки.

Пути задаются в `.env`:

```env
APP_SECRET_KEY_FILE=/app/secrets/session.key
APP_ENCRYPTION_KEY_WRAP_FILE=/app/secrets/encryption.key.wrap
PANEL_KEY_PASSPHRASE_FILE=/app/secrets/unlock.passphrase
```

## Что мы **не** обещаем

- **Root / RCE на VPS** — полный доступ к хосту даёт чтение `secrets/`, `unlock.passphrase` и памяти процесса. Это ограничение любой single-server схемы без внешнего Vault/HSM.
- **SQLCipher** — шифруется не вся БД, а отдельные колонки с секретами (Fernet).
- **Защита от физического доступа** к серверу без шифрования диска — вне scope приложения.

## Угрозы и митигации

| Угроза | Митигация |
|--------|-----------|
| Копия `data/` без `secrets/` | Данные в БД не расшифровать |
| Копия `encryption.key.wrap` без пароля | Brute-force пароля (PBKDF2 600k итераций) |
| Ключи в `.env` в git / бэкапах | Миграция в `secrets/`, предупреждения при старте |
| CSRF, брутфорс login | CSRF на POST, rate limit на `/login` |
| SSRF через URL провайдера | Whitelist, блок private IP |
| IDOR | Проверка владельца записей |
| Утечка `.db.enc` в Telegram | Нужен master-ключ; смените token бота при компрометации |

## Установка с нуля

`install.sh` запрашивает **пароль разблокировки** (отдельно от пароля администратора), создаёт `encryption.key.wrap` и `unlock.passphrase`.

Сохраните пароль разблокировки **вне сервера** (менеджер паролей). Без него после потери `unlock.passphrase` восстановить доступ к зашифрованным полям БД невозможно.

## Миграция существующего сервера

```bash
cd /opt/server-billing
git pull
bash scripts/migrate-keys-to-files.sh .
bash scripts/wrap-encryption-key.sh .
chown -R 1000:1000 data secrets
docker compose -f docker-compose.prod.yml up -d --build
```

Проверьте логи: не должно быть `wrap-no-passphrase` или `wrap-locked`.

## Усиление (по желанию)

1. **Пароль только в памяти** — не создавать `unlock.passphrase`; передавать `PANEL_KEY_PASSPHRASE` при ручном `docker compose up` (неудобно при autorestart).
2. **Docker secret** — `PANEL_KEY_PASSPHRASE_FILE=/run/secrets/panel_key_passphrase`, файл вне каталога `secrets/` с `.wrap`.
3. **Шифрование диска** (LUKS) на уровне хостинга.
4. **Vault / KMS** — для команд с инфраструктурой; панель не интегрируется с Vault из коробки.

## Legacy

Поддерживаются для плавного обновления:

- `APP_ENCRYPTION_KEY` / `APP_SECRET_KEY` в `.env`
- `secrets/encryption.key` без обёртки (логируется предупреждение)

Переход на wrap **настоятельно рекомендуется** перед prod и для всех новых установок.

## Ответственность распространителя

Если вы отдаёте панель другим людям:

- Не коммитьте `secrets/`, `.env`, `data/` в git.
- Документируйте необходимость **отдельного** пароля разблокировки и бэкапа `secrets/` + пароля.
- Напоминайте: смена `encryption.key` после накопления данных делает старые записи нечитаемыми.

Подробнее о сетевых настройках и чеклисте — [README.md → Безопасность](README.md#безопасность).
