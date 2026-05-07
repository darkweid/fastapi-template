from sqlalchemy.ext.asyncio import create_async_engine

from src.main.config import config

DATABASE_URL = config.postgres.dsn_async
POOL_TIMEOUT_SECONDS = 30
POOL_RECYCLE_SECONDS = 60 * 30

engine = create_async_engine(
    DATABASE_URL,
    echo=config.postgres.DB_ECHO,
    pool_size=5,
    max_overflow=2,
    pool_timeout=POOL_TIMEOUT_SECONDS,
    pool_recycle=POOL_RECYCLE_SECONDS,
    pool_pre_ping=True,
)

celery_engine = create_async_engine(
    DATABASE_URL,
    echo=config.postgres.DB_ECHO,
    pool_size=2,
    max_overflow=2,
    pool_timeout=POOL_TIMEOUT_SECONDS,
    pool_recycle=POOL_RECYCLE_SECONDS,
    pool_pre_ping=True,
)
