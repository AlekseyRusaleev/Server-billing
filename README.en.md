<img width="1874" height="934" alt="Server Billing Manager dashboard" src="https://github.com/user-attachments/assets/3b7ca0f4-1c3d-4087-9002-416d0066f5fd" />

# Server Billing Manager

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/downloads/)
[![Docker](https://img.shields.io/badge/docker-compose-blue.svg)](docker-compose.prod.yml)
[![Security](https://img.shields.io/badge/security-SECURITY.md-green.svg)](SECURITY.md)

**Self-hosted panel to track VPS/hosting renewals, payment links, and provider accounts.**

The app does **not** process payments or store bank cards. It reminds you before due dates, keeps billing URLs, syncs services from provider APIs (read-only), and sends Telegram alerts.

**Русская документация:** [README.md](README.md)

---

## Quick start (one command)

On a fresh Linux VPS as `root`:

```bash
curl -fsSL https://raw.githubusercontent.com/AlekseyRusaleev/Server-billing/main/scripts/install.sh | bash
```

The installer asks for: optional domain, admin username/password, and an **encryption unlock passphrase** (min. 12 characters — store it safely).

Default HTTPS without a domain: `https://YOUR_SERVER_IP.sslip.io`

Manual install: see [README.md § Manual install](README.md#ручная-установка-без-installsh).

---

## Why this project

| Problem | Solution |
|---------|----------|
| Many VPS across providers | Single dashboard with filters and statuses |
| Forgotten renewals | Telegram reminders + iCal export |
| Scattered billing URLs | Per-server and per-account payment links |
| Manual copy-paste from provider panels | BILLmanager + Web API sync (read-only) |
| Secrets in plain `.env` | Fernet in SQLite + passphrase-wrapped master key |

---

## Integrations

| Type | Description | Status |
|------|-------------|--------|
| **Manual** | Links, dates, credentials entered by hand | Built-in |
| **BILLmanager** | Compatible billing APIs — services, IPs, renewal dates | Built-in |
| **Web API** | Provider REST APIs (API key in encrypted field) | Per-provider connectors in catalog |
| **Telegram** | Reminders, sync summaries, encrypted DB backup | Built-in |
| **iCal** | Export payment calendar to Google/Apple/Outlook | Built-in |
| **SSL monitor** | Expiry alerts for server domains | Built-in |

Providers with live sync are marked **API sync** in the catalog. See [Provider integrations (RU)](README.md#интеграция-с-провайдерами).

To add a provider: [CONTRIBUTING.md](CONTRIBUTING.md).

---

## Features (summary)

- Server dashboard, payment history, analytics, monthly cost forecast.
- Hosting accounts shared across multiple servers.
- Provider catalog (~36 hosts, ~57 flags, filters, remote bundle updates without rebuild).
- Currencies: RUB, USD, EUR, USDT (CBR + CoinGecko rates).
- Encrypted backups (`.db.enc`) to Telegram.
- Web SSH terminal from the panel (off by default).
- IP allowlist, CSRF, login rate limit, security headers.
- Docker Compose + **Caddy** or **nginx** on existing :443.
- In-panel `git pull` updates.

Full feature list: [README.md](README.md#возможности).

---

## Security

- Master keys in `secrets/` — encryption key wrapped with unlock passphrase ([SECURITY.md](SECURITY.md)).
- SSH passwords and API keys encrypted in SQLite (Fernet).
- Fail-closed without admin password hash and session key.

Production checklist: [README.md § Security](README.md#безопасность).

---

## Stack

- **Backend:** Python 3.12, FastAPI, SQLite
- **Frontend:** Jinja2 templates, vanilla JS
- **Deploy:** Docker Compose, Caddy or nginx, optional in-container updater

---

## Project status

**v1.0.0** — stable for personal/small-team self-hosting. Single admin account.

Issues and PRs are welcome; maintenance is best-effort ([CONTRIBUTING.md](CONTRIBUTING.md)).

---

## Support the author

Optional: [Telegram Stars](https://t.me/AlekseyRdonate_bot)

---

## License

[MIT](LICENSE) — Copyright (c) 2025 Aleksey Rusaleev
