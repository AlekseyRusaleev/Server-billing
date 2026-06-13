#!/usr/bin/env bash
# Оборачивает secrets/encryption.key паролем → secrets/encryption.key.wrap (сырой ключ удаляется)
set -euo pipefail

INSTALL_DIR="${1:-$(cd "$(dirname "$0")/.." && pwd)}"
SECRETS_DIR="$INSTALL_DIR/secrets"
ENV_FILE="$INSTALL_DIR/.env"
RAW_KEY="$SECRETS_DIR/encryption.key"
WRAP_KEY="$SECRETS_DIR/encryption.key.wrap"

if [ ! -f "$RAW_KEY" ]; then
  echo "Не найден $RAW_KEY — сначала migrate-keys-to-files.sh или install.sh" >&2
  exit 1
fi

if [ -f "$WRAP_KEY" ]; then
  echo "Уже существует $WRAP_KEY" >&2
  exit 1
fi

read -r -s -p "Пароль разблокировки (мин. 12 символов): " PASS1
echo
read -r -s -p "Повторите пароль: " PASS2
echo
if [ "$PASS1" != "$PASS2" ]; then
  echo "Пароли не совпадают." >&2
  exit 1
fi

cd "$INSTALL_DIR"
python3 - <<PY
from pathlib import Path
from app.key_wrap import wrap_encryption_key

dek = Path(r"$RAW_KEY").read_text(encoding="utf-8").strip()
Path(r"$WRAP_KEY").write_bytes(wrap_encryption_key(dek, """$PASS1"""))
PY

rm -f "$RAW_KEY"
chmod 600 "$WRAP_KEY"
chown 1000:1000 "$WRAP_KEY" 2>/dev/null || true

python3 - <<PY
from pathlib import Path

env_path = Path(r"$ENV_FILE")
if not env_path.is_file():
    raise SystemExit(0)
lines = []
has_wrap = False
for line in env_path.read_text(encoding="utf-8").splitlines():
    if line.startswith("APP_ENCRYPTION_KEY_FILE="):
        continue
    if line.startswith("APP_ENCRYPTION_KEY_WRAP_FILE="):
        has_wrap = True
    lines.append(line)
if not has_wrap:
    lines.append("APP_ENCRYPTION_KEY_WRAP_FILE=/app/secrets/encryption.key.wrap")
    lines.append("PANEL_KEY_PASSPHRASE_FILE=/app/secrets/unlock.passphrase")
env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY

read -r -s -p "Сохранить пароль в secrets/unlock.passphrase? [Y/n]: " SAVE_PP
echo
if [ "${SAVE_PP:-Y}" != "n" ] && [ "${SAVE_PP:-Y}" != "N" ]; then
  umask 077
  printf '%s' "$PASS1" > "$SECRETS_DIR/unlock.passphrase"
  chmod 600 "$SECRETS_DIR/unlock.passphrase"
  chown 1000:1000 "$SECRETS_DIR/unlock.passphrase" 2>/dev/null || true
fi

echo "Готово: $WRAP_KEY"
echo "Пароль разблокировки нужен при каждом перезапуске (файл unlock.passphrase или PANEL_KEY_PASSPHRASE)."
