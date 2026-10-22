from redis.asyncio import Redis

from app.core.config import get_settings


settings = get_settings()


# None when Redis is turned off (REDIS_URL empty or "disabled").
redis_client: Redis | None = (
    Redis.from_url(
        settings.redis_url,
        encoding="utf-8",
        decode_responses=True,
    )
    if settings.redis_enabled
    else None
)
