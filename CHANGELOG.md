# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [1.0.0] - 2026-06-13

First stable release for public self-hosted use.

### Added

- Dashboard for VPS/hosting renewals, payment links, and provider accounts.
- Provider catalog (~36 hosts, ~57 country flags, filters, remote JSON bundle updates).
- BILLmanager-compatible and Web API provider sync (read-only).
- Telegram reminders, encrypted SQLite backups to Telegram, SSL certificate monitoring.
- iCal export for payment calendar, multi-currency support (RUB, USD, EUR, USDT).
- One-command install (`scripts/install.sh`), Docker Compose + Caddy or nginx.
- Security hardening: CSRF, login rate limit, IP allowlist, SSRF protection, Fernet encryption.
- Passphrase-wrapped encryption master key (`encryption.key.wrap`, `SECURITY.md`).
- In-panel updates via updater service, web SSH terminal (disabled by default).

### Security

- Master keys in `secrets/`, not in `.env`.
- Fail-closed auth without session key and admin password hash.
- Column-level Fernet encryption for SSH passwords, API keys, and bot tokens.

[1.0.0]: https://github.com/AlekseyRusaleev/Server-billing/releases/tag/v1.0.0
