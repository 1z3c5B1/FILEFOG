"""Глобальная конфигурация приложения (читается из .env)."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(BASE_DIR / ".env", ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Telegram
    bot_token: str = ""
    bot_username: str = "trambot"
    admins: str = ""

    # Web
    web_host: str = "0.0.0.0"
    web_port: int = 8000
    public_base_url: str = "http://127.0.0.1:8000"
    secret_key: str = "dev-secret-change-me"
    # Кнопка-мини-апп в боте. Telegram требует HTTPS (localhost можно для отладки).
    webapp_enabled: bool = True
    webapp_path: str = ""  # пусто = public_base_url

    # Storage
    storage_dir: str = "./storage"
    max_file_mb: int = 100
    user_quota_mb: int = 2048

    # OAuth
    google_client_id: str = ""
    google_client_secret: str = ""
    google_redirect_uri: str = ""
    github_client_id: str = ""
    github_client_secret: str = ""
    github_redirect_uri: str = ""
    discord_client_id: str = ""
    discord_client_secret: str = ""
    discord_redirect_uri: str = ""
    yandex_client_id: str = ""
    yandex_client_secret: str = ""
    yandex_redirect_uri: str = ""

    # Derived
    db_path: str = str(BASE_DIR / "data" / "trambot.db")

    @model_validator(mode="after")
    def _apply_platform_env(self) -> "Settings":
        """Railway / Render / Fly и прочие задают порт через $PORT."""
        port = os.getenv("PORT", "").strip()
        if port.isdigit():
            self.web_port = int(port)
        host = os.getenv("HOST", "").strip()
        if host:
            self.web_host = host
        return self

    @property
    def storage_path(self) -> Path:
        p = Path(self.storage_dir)
        return p if p.is_absolute() else (BASE_DIR / p)

    @property
    def admin_ids(self) -> set[int]:
        out: set[int] = set()
        for part in self.admins.replace(" ", "").split(","):
            if part.isdigit():
                out.add(int(part))
        return out

    def callback_url(self, provider: str) -> str:
        base = self.public_base_url.rstrip("/")
        explicit = getattr(self, f"{provider}_redirect_uri", "")
        if explicit:
            return explicit
        return f"{base}/auth/{provider}/callback"

    def is_provider_configured(self, provider: str) -> bool:
        return bool(getattr(self, f"{provider}_client_id", "")) and bool(
            getattr(self, f"{provider}_client_secret", "")
        )

    @property
    def webapp_url(self) -> str:
        """Адрес мини-аппа для кнопки в боте."""
        base = self.public_base_url.rstrip("/")
        path = self.webapp_path.strip()
        if not path:
            return base
        if path.startswith(("http://", "https://")):
            return path
        return f"{base}/{path.lstrip('/')}"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
