from sqlalchemy.pool import NullPool
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings


settings = get_settings()


# Tests run each TestClient on its own event loop; pooled asyncpg
# connections cannot cross loops, so the test environment disables pooling.
engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    **({"poolclass": NullPool} if settings.environment == "test" else {}),
)


AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
