#!/usr/bin/env bash
# Перенос APP_SECRET_KEY и APP_ENCRYPTION_KEY из .env в secrets/*.key
set -euo pipefail

INSTALL_DIR="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
ENV_FILE="$INSTALL_DIR/.env"
SECRETS_DIR="$INSTALL_DIR/secrets"

if [ ! -f "$ENV_FILE" ]; then
  echo "Не найден $ENV_FILE" >&2
  exit 1
fi

read_env_value() {
  local key="$1"
  grep -E "^${key}=" "$ENV_FILE" | head -n1 | cut -d= -f2- || true
}

session_key="$(read_env_value APP_SECRET_KEY)"
encryption_key="$(read_env_value APP_ENCRYPTION_KEY)"

if [ -z "$session_key" ] && [ -z "$encryption_key" ]; then
  echo "В .env нет APP_SECRET_KEY / APP_ENCRYPTION_KEY — возможно, уже перенесено."
  exit 0
fi

mkdir -p "$SECRETS_DIR"
chmod 700 "$SECRETS_DIR"
umask 077

if [ -n "$encryption_key" ]; then
  printf '%s' "$encryption_key" > "$SECRETS_DIR/encryption.key"
  chmod 600 "$SECRETS_DIR/encryption.key"
  echo "Записан secrets/encryption.key"
fi

if [ -n "$session_key" ]; then
  printf '%s' "$session_key" > "$SECRETS_DIR/session.key"
  chmod 600 "$SECRETS_DIR/session.key"
  echo "Записан secrets/session.key"
fi

chown -R 1000:1000 "$SECRETS_DIR" 2>/dev/null || true

python3 - <<PY
from pathlib import Path

env_path = Path(r"$ENV_FILE")
lines = env_path.read_text(encoding="utf-8").splitlines()
out = []
has_session_file = has_encryption_file = False
for line in lines:
    if line.startswith("APP_SECRET_KEY="):
        continue
    if line.startswith("APP_ENCRYPTION_KEY="):
        continue
    if line.startswith("APP_SECRET_KEY_FILE="):
        has_session_file = True
    if line.startswith("APP_ENCRYPTION_KEY_FILE="):
        has_encryption_file = True
    out.append(line)
if not has_session_file and r"$session_key":
    out.append("APP_SECRET_KEY_FILE=/app/secrets/session.key")
if not has_encryption_file and r"$encryption_key":
    out.append("APP_ENCRYPTION_KEY_FILE=/app/secrets/encryption.key")
env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
PY

echo "Обновлён .env — ключи убраны, добавлены *_FILE."
echo "Перезапуск: docker compose -f docker-compose.prod.yml up -d"
