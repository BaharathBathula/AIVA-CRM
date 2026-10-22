from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "AIVA CRM API"
    environment: str = "development"
    jwt_secret_key: str = ""
    jwt_access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 30
    app_base_url: str = "http://localhost:3000"
    max_failed_logins: int = 5
    lockout_minutes: int = 15
    api_v1_prefix: str = "/api/v1"

    database_url: str = (
        "postgresql+asyncpg://aiva:aiva_local_dev@postgres:5432/aiva_crm"
    )

    # Optional: set to "" or "disabled" to run without Redis (e.g. small hosted demos).
    redis_url: str = "redis://redis:6379/0"

    # Return invitation / password-reset links in API responses even outside
    # development. Only for demos with no email provider configured.
    show_links_without_email: bool = False

    cors_origins: list[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


    @field_validator("database_url")
    @classmethod
    def use_async_driver(cls, value: str) -> str:
        """Accept plain postgres:// URLs (as hosting providers give them)."""
        for prefix in ("postgres://", "postgresql://"):
            if value.startswith(prefix):
                value = "postgresql+asyncpg://" + value[len(prefix):]
        # asyncpg doesn't understand libpq's sslmode parameter.
        if "sslmode=" in value:
            base, _, query = value.partition("?")
            kept = [part for part in query.split("&") if part and not part.startswith("sslmode=")]
            value = base + ("?" + "&".join(kept) if kept else "")
        return value

    @property
    def redis_enabled(self) -> bool:
        return bool(self.redis_url) and self.redis_url.lower() not in {"disabled", "none", "off"}

    @property
    def expose_dev_links(self) -> bool:
        """Return password-reset and invite links in API responses.

        In development and test, where no email provider is configured, or
        when SHOW_LINKS_WITHOUT_EMAIL is set for a demo deployment.
        """
        return self.environment in {"development", "test"} or self.show_links_without_email


@lru_cache
def get_settings() -> Settings:
    return Settings()
