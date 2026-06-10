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
    apt-get install -y ca-certificates curl git openssh-client
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y ca-certificates curl git openssh-clients
  elif command -v yum >/dev/null 2>&1; then
    yum install -y ca-certificates curl git openssh-clients
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
  local bot_token="$3"
  local chat_id="$4"
  local base_url site_address server_ip

  server_ip="$(curl -fsS --max-time 5 https://api.ipify.org || hostname -I | awk '{print $1}')"

  if [ -n "$domain" ]; then
    site_address="$domain"
    base_url="https://$domain"
  else
    site_address="$server_ip.sslip.io"
    base_url="https://$site_address"
  fi

  umask 077
  cat > "$INSTALL_DIR/.env" <<EOF
APP_NAME=Server Billing Manager
DATABASE_PATH=/app/data/server_billing.db
BASE_URL=$base_url
SERVER_IP=$server_ip
CADDY_SITE_ADDRESS=$site_address
CADDY_EMAIL=$email
TELEGRAM_BOT_TOKEN=$bot_token
TELEGRAM_CHAT_ID=$chat_id
REMINDER_DAYS=7,3,1,0,-1
CHECK_INTERVAL_SECONDS=86400
EOF
}

main() {
  echo "Server Billing Manager installer"
  echo
  local domain email bot_token chat_id
  domain="$(prompt 'Domain for HTTPS, leave empty to use automatic IP.sslip.io HTTPS' '')"
  email=""
  if [ -n "$domain" ]; then
    email="$(prompt 'Email for Lets Encrypt notifications' '')"
  fi
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

  write_env "$domain" "$email" "$bot_token" "$chat_id"

  cd "$INSTALL_DIR"
  docker compose -f docker-compose.prod.yml up -d --build

  echo
  echo "Done."
  echo "Open: $(grep '^BASE_URL=' .env | cut -d= -f2-)"
  echo "Project directory: $INSTALL_DIR"
  echo "Update later with: cd $INSTALL_DIR && git pull && docker compose -f docker-compose.prod.yml up -d --build"
}

main "$@"
