#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/AlekseyRusaleev/Server-billing.git}"
INSTALL_DIR="${INSTALL_DIR:-/opt/server-billing}"
SERVICE_NAME="server-billing"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this installer as root: sudo bash install.sh"
  exit 1
fi

prompt() {
  local label="$1"
  local default="${2:-}"
  local value
  if [ -n "$default" ]; then
    read -r -p "$label [$default]: " value
    echo "${value:-$default}"
  else
    read -r -p "$label: " value
    echo "$value"
  fi
}

prompt_secret() {
  local label="$1"
  local value
  read -r -s -p "$label: " value
  echo
  echo "$value"
}

install_packages() {
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update
    apt-get install -y ca-certificates curl git openssh-client python3
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y ca-certificates curl git openssh-clients python3
  elif command -v yum >/dev/null 2>&1; then
    yum install -y ca-certificates curl git openssh-clients python3
  else
    echo "Unsupported Linux distribution. Install Docker, Docker Compose and Git manually."
    exit 1
  fi
}

install_docker() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    return
  fi
  curl -fsSL https://get.docker.com | sh
  systemctl enable --now docker
}

write_env() {
  local domain="$1"
  local email="$2"
  local admin_username="$3"
  local admin_password_hash="$4"
  local app_secret_key="$5"
  local app_encryption_key="$6"
  local bot_token="$7"
  local chat_id="$8"
  local base_url site_address server_ip currency_rates currency_rates_updated_at update_token

  server_ip="$(curl -fsS --max-time 5 https://api.ipify.org || hostname -I | awk '{print $1}')"

  if [ -n "$domain" ]; then
    site_address="$domain"
    base_url="https://$domain"
  else
    site_address="$server_ip.sslip.io"
    base_url="https://$site_address"
  fi

  currency_rates="$(python3 - <<'PY'
import json
import urllib.request
import xml.etree.ElementTree as ET
COINGECKO_USDT_RUB_URL = 'https://api.coingecko.com/api/v3/simple/price?ids=tether&vs_currencies=rub'
try:
    with urllib.request.urlopen('https://www.cbr.ru/scripts/XML_daily.asp', timeout=15) as response:
        raw = response.read().decode('windows-1251')
    root = ET.fromstring(raw)
    rates = {'RUB': 1.0}
    for item in root.findall('Valute'):
        code = item.findtext('CharCode', '').strip().upper()
        nominal = float(item.findtext('Nominal', '1').replace(',', '.'))
        value = float(item.findtext('Value', '0').replace(',', '.'))
        if code and nominal:
            rates[code] = value / nominal
    try:
        request = urllib.request.Request(COINGECKO_USDT_RUB_URL, headers={'User-Agent': 'server-billing-manager/1.0'})
        with urllib.request.urlopen(request, timeout=15) as response:
            usdt = json.loads(response.read().decode('utf-8')).get('tether', {}).get('rub')
        if usdt:
            rates['USDT'] = float(usdt)
    except Exception:
        if 'USD' in rates:
            rates['USDT'] = rates['USD']
    print(','.join(f'{code}:{rate:.8f}' for code, rate in sorted(rates.items())))
except Exception:
    print('RUB:1')
PY
)"
  currency_rates_updated_at="$(date +%F)"
  update_token="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
)"

  umask 077
  cat > "$INSTALL_DIR/.env" <<EOF
APP_NAME=Server Billing Manager
DATABASE_PATH=/app/data/server_billing.db
BASE_URL=$base_url
SERVER_IP=$server_ip
CADDY_SITE_ADDRESS=$site_address
CADDY_EMAIL=$email
APP_SECRET_KEY=$app_secret_key
APP_ENCRYPTION_KEY=$app_encryption_key
ADMIN_USERNAME=$admin_username
ADMIN_PASSWORD_HASH=$admin_password_hash
TELEGRAM_BOT_TOKEN=$bot_token
TELEGRAM_CHAT_ID=$chat_id
REMINDER_DAYS=7,3,1,0,-1
CHECK_INTERVAL_SECONDS=86400
BACKUP_INTERVAL_DAYS=7
CURRENCY_BASE=RUB
CURRENCY_RATES=$currency_rates
CURRENCY_RATES_UPDATED_AT=$currency_rates_updated_at
APP_UPDATE_URL=http://updater:8765/update
APP_UPDATE_TOKEN=$update_token
EOF
}

main() {
  echo "Server Billing Manager installer"
  echo
  local domain email admin_username admin_password admin_password_repeat admin_password_hash app_secret_key app_encryption_key bot_token chat_id
  domain="$(prompt 'Domain for HTTPS, leave empty to use automatic IP.sslip.io HTTPS' '')"
  email=""
  if [ -n "$domain" ]; then
    email="$(prompt 'Email for Lets Encrypt notifications' '')"
  fi
  admin_username="$(prompt 'Admin login' 'admin')"
  while true; do
    admin_password="$(prompt_secret 'Admin password')"
    admin_password_repeat="$(prompt_secret 'Repeat admin password')"
    if [ -n "$admin_password" ] && [ "$admin_password" = "$admin_password_repeat" ]; then
      break
    fi
    echo "Passwords are empty or do not match. Try again."
  done
  admin_password_hash="$(ADMIN_PASSWORD_INPUT="$admin_password" python3 - <<'PY'
import base64, hashlib, os
password = os.environ["ADMIN_PASSWORD_INPUT"].encode()
salt = os.urandom(16)
iterations = 260_000
digest = hashlib.pbkdf2_hmac("sha256", password, salt, iterations)
print("pbkdf2_sha256:{}:{}:{}".format(
    iterations,
    base64.urlsafe_b64encode(salt).decode(),
    base64.urlsafe_b64encode(digest).decode(),
))
PY
)"
  app_secret_key="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(48))
PY
)"
  app_encryption_key="$(python3 - <<'PY'
import base64, os
print(base64.urlsafe_b64encode(os.urandom(32)).decode())
PY
)"
  bot_token="$(prompt_secret 'Telegram bot token, leave empty to disable reminders')"
  chat_id=""
  if [ -n "$bot_token" ]; then
    chat_id="$(prompt 'Telegram chat id' '')"
  fi

  install_packages
  install_docker

  mkdir -p "$INSTALL_DIR"
  if [ -d "$INSTALL_DIR/.git" ]; then
    git -C "$INSTALL_DIR" pull --ff-only
  else
    git clone "$REPO_URL" "$INSTALL_DIR"
  fi

  write_env "$domain" "$email" "$admin_username" "$admin_password_hash" "$app_secret_key" "$app_encryption_key" "$bot_token" "$chat_id"

  cd "$INSTALL_DIR"
  docker compose -f docker-compose.prod.yml up -d --build

  echo
  echo "Done."
  echo "Open: $(grep '^BASE_URL=' .env | cut -d= -f2-)"
  echo "Login: $admin_username"
  echo "Project directory: $INSTALL_DIR"
  echo "Update later from the web panel or with: cd $INSTALL_DIR && git pull && docker compose -f docker-compose.prod.yml up -d --build"
}

main "$@"
