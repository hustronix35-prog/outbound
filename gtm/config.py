from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    product_name: str = "Your Product"
    product_description: str = ""
    target_market: str = ""
    booking_link: str = ""
    product_website: str = ""
    sender_name: str = "Founder"
    sender_title: str = "Founder & CEO"

    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_provider: str = ""  # anthropic | openai (auto-detect from key if empty)
    llm_model_cheap: str = "gpt-4o-mini"
    llm_model_quality: str = "gpt-4o-mini"
    daily_budget_usd: float = 5.0

    bettercontact_api_key: str = ""
    hunter_api_key: str = ""
    apollo_api_key: str = ""
    apify_token: str = ""

    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""
    imap_host: str = "imap.gmail.com"
    imap_port: int = 993
    imap_user: str = ""
    imap_password: str = ""

    # Microsoft Graph / Outlook (preferred when set)
    ms_client_id: str = ""
    ms_client_secret: str = ""
    ms_tenant_id: str = ""
    ms_refresh_token: str = ""
    ms_mailbox: str = ""
    booking_from_name: str = ""

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    telegram_notify_sends: bool = True
    telegram_notify_replies: bool = True
    telegram_notify_bookings: bool = True
    telegram_notify_summary: bool = True

    max_discover_per_run: int = 50
    max_email_lookups_per_day: int = 25
    max_sends_per_day: int = 20
    qualify_auto_accept: float = 0.80
    qualify_auto_reject: float = 0.30
    personalize_top_n_social: int = 5
    follow_up_hours: int = 48
    dry_run: bool = True

    database_url: str = Field(default_factory=lambda: f"sqlite:///{DATA_DIR / 'gtm.db'}")
    admin_host: str = "127.0.0.1"
    admin_port: int = 8080
    log_level: str = "INFO"

    def ensure_dirs(self) -> None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        (DATA_DIR / "cache").mkdir(exist_ok=True)
        (DATA_DIR / "exports").mkdir(exist_ok=True)

    @property
    def mail_transport(self) -> str:
        if self.ms_client_id and self.ms_refresh_token:
            return "ms_graph"
        if self.smtp_user:
            return "smtp"
        return "none"


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.ensure_dirs()
    return s
