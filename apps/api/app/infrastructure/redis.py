from redis.asyncio import Redis

from app.core.config import get_settings


settings = get_settings()


redis_client = Redis.from_url(
    settings.redis_url,
    encoding="utf-8",
    decode_responses=True,
)
