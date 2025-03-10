from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base  # noqa
from .settings import settings

DATABASE_URL_SYNC = settings.build_postgres_dsn_sync()

# Synchronous engine for special use cases ONLY.
# IMPORTANT: This engine is intended exclusively for synchronous tasks, such as Celery workers or scripts.
# Do NOT use this synchronous engine within asynchronous FastAPI endpoints or other async code paths.

engine_sync = create_engine(
    DATABASE_URL_SYNC,
    future=True,
    echo=settings.db_echo,
    pool_size=10,
    max_overflow=10,
    pool_timeout=30,
    pool_recycle=1800
)

# Use this session factory ONLY in synchronous contexts
SessionLocalSync = sessionmaker(
    bind=engine_sync,
    autoflush=False,
    autocommit=False,
    future=True,
)
