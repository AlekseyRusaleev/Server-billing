from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Server Billing Manager"
    database_path: str = "./data/server_billing.db"
    base_url: str = "http://127.0.0.1:8000"
    server_ip: str = "127.0.0.1"
    caddy_site_address: str = "127.0.0.1.sslip.io"
    caddy_email: str = ""
    app_secret_key: str = ""
    app_encryption_key: str = ""
    admin_username: str = "admin"
    admin_password_hash: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    reminder_days: str = "7,3,1,0,-1"
    check_interval_seconds: int = 86400
    backup_interval_days: int = 7
    currency_base: str = "RUB"
    currency_rates: str = "RUB:1"
    currency_rates_updated_at: str = ""
    app_update_url: str = ""
    app_update_token: str = ""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
