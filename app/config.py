from __future__ import annotations

import os
import secrets
from dataclasses import dataclass


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def _database_url() -> str:
    url = _env("DATABASE_URL", "sqlite:///./data/squad.sqlite3")
    # Railway fournit postgresql:// ; SQLAlchemy veut le driver explicite (psycopg 3).
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


@dataclass(frozen=True)
class Settings:
    database_url: str
    region: str
    locale: str
    blizzard_client_id: str | None
    blizzard_client_secret: str | None
    public_url: str
    site_password: str | None
    secret_key: str
    squad_name: str
    refresh_interval: int
    telegram_bot_token: str | None
    telegram_chat_id: str | None
    discord_webhook_url: str | None
    live_events: bool
    recap_weekday: int
    recap_hour: int
    timezone: str

    @property
    def blizzard_enabled(self) -> bool:
        return bool(self.blizzard_client_id and self.blizzard_client_secret)

    @property
    def battlenet_login_enabled(self) -> bool:
        return self.blizzard_enabled and bool(self.public_url)

    @property
    def oauth_redirect_uri(self) -> str:
        return self.public_url.rstrip("/") + "/auth/callback"


def load_settings() -> Settings:
    public_url = _env("PUBLIC_URL", "")
    if not public_url and _env("RAILWAY_PUBLIC_DOMAIN"):
        public_url = "https://" + _env("RAILWAY_PUBLIC_DOMAIN")
    return Settings(
        database_url=_database_url(),
        region=_env("BLIZZARD_REGION", "eu").lower(),
        locale=_env("BLIZZARD_LOCALE", "fr_FR"),
        blizzard_client_id=_env("BLIZZARD_CLIENT_ID"),
        blizzard_client_secret=_env("BLIZZARD_CLIENT_SECRET"),
        public_url=public_url,
        site_password=_env("SITE_PASSWORD"),
        # Sans SECRET_KEY, les sessions sautent à chaque redémarrage : à définir sur Railway.
        secret_key=_env("SECRET_KEY") or secrets.token_hex(32),
        squad_name=_env("SQUAD_NAME", "La Squad"),
        refresh_interval=max(_int("REFRESH_INTERVAL", 900), 120),
        telegram_bot_token=_env("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=_env("TELEGRAM_CHAT_ID"),
        discord_webhook_url=_env("DISCORD_WEBHOOK_URL"),
        live_events=_env("LIVE_EVENTS", "true").lower() not in ("0", "false", "no"),
        recap_weekday=_int("RECAP_WEEKDAY", 2),  # 0 = lundi ; mercredi = reset EU
        recap_hour=_int("RECAP_HOUR", 10),
        timezone=_env("TZ_NAME", "Europe/Paris"),
    )


settings = load_settings()
