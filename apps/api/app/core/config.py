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
    # Largest file a user can upload to Documents, in megabytes.
    document_max_mb: int = 25
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

    # Encryption key for secrets and encrypted fields (Fernet key or any
    # long random string). Defaults to a key derived from JWT_SECRET_KEY.
    field_encryption_key: str = ""

    # Billing. Without Stripe keys, plan changes apply immediately with no
    # charge (demo billing). With them, upgrades go through Stripe Checkout.
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""

    # Outgoing email used for reminders and campaigns when a user hasn't
    # connected their own mailbox. Leave empty to keep mail in the outbox.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_email: str = ""
    smtp_from_name: str = "AIVA CRM"
    smtp_security: str = "starttls"  # starttls | ssl | none

    # Claude for AI drafting, summaries and "Ask AIVA". Built-in rules are
    # used when no key is set.
    anthropic_api_key: str = ""
    ai_model: str = "claude-haiku-5-5"

    # Sign in with Google, Microsoft and Apple. Each button works once its
    # credentials are set; the redirect URI to register with each provider is
    # {APP_BASE_URL}/api/session/oauth/{provider}/callback.
    google_client_id: str = ""
    google_client_secret: str = ""
    microsoft_client_id: str = ""
    microsoft_client_secret: str = ""
    microsoft_tenant_id: str = "common"
    apple_client_id: str = ""  # the Services ID
    apple_team_id: str = ""
    apple_key_id: str = ""
    apple_private_key: str = ""  # contents of the .p8 key file

    # Background jobs (reminders, campaigns, journeys, chat alerts).
    scheduler_enabled: bool = True
    scheduler_interval_seconds: int = 30

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
        # Translate libpq-only query parameters (e.g. Neon's
        # "?sslmode=require&channel_binding=require") for asyncpg.
        base, _, query = value.partition("?")
        if query:
            kept = []
            for part in query.split("&"):
                key, _, val = part.partition("=")
                if not part or key == "channel_binding":
                    continue
                kept.append(f"ssl={val}" if key == "sslmode" else part)
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
