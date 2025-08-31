from sqlalchemy.ext.asyncio import create_async_engine

from src.main.config import config

DATABASE_URL = config.postgres.dsn_async

engine = create_async_engine(
    DATABASE_URL,
    echo=config.postgres.DB_ECHO,
    pool_size=10,
    max_overflow=10,
    pool_timeout=30,
    pool_recycle=60 * 30,  # Restart the pool after 30 minutes
)
