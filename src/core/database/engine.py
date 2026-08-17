from sqlalchemy.ext.asyncio import create_async_engine

from src.main.config import config

DATABASE_URL = config.postgres.dsn_async
POOL_TIMEOUT_SECONDS = 30
POOL_RECYCLE_SECONDS = 60 * 30

engine = create_async_engine(
    DATABASE_URL,
    echo=config.postgres.DB_ECHO,
    pool_size=config.postgres.DB_POOL_SIZE,
    max_overflow=config.postgres.DB_MAX_OVERFLOW,
    pool_timeout=POOL_TIMEOUT_SECONDS,
    pool_recycle=POOL_RECYCLE_SECONDS,
    pool_pre_ping=True,
)

# Isolated pool for background-task workers. Defaults sum to 20 to match
# --max-async-tasks 20 on the worker command (infra/docker-compose.yml):
# even if every concurrent task grabs a session, nobody hits pool_timeout.
tasks_engine = create_async_engine(
    DATABASE_URL,
    echo=config.postgres.DB_ECHO,
    pool_size=config.postgres.DB_TASKS_POOL_SIZE,
    max_overflow=config.postgres.DB_TASKS_MAX_OVERFLOW,
    pool_timeout=POOL_TIMEOUT_SECONDS,
    pool_recycle=POOL_RECYCLE_SECONDS,
    pool_pre_ping=True,
)
