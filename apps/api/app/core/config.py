from functools import lru_cache

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

    redis_url: str = "redis://redis:6379/0"

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


    @property
    def expose_dev_links(self) -> bool:
        """Return password-reset and invite links in API responses.

        Only in development and test, where no email provider is configured.
        """
        return self.environment in {"development", "test"}


@lru_cache
def get_settings() -> Settings:
    return Settings()
