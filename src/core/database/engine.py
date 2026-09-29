import math
from typing import Any

from sqlalchemy.ext.asyncio import create_async_engine

from src.main.config import config

DATABASE_URL = config.postgres.dsn_async
POOL_TIMEOUT_SECONDS = 30
POOL_RECYCLE_SECONDS = 60 * 30


def session_timeouts(
    *,
    statement_timeout_seconds: float = 0,
    idle_in_transaction_timeout_seconds: float = 0,
) -> dict[str, Any]:
    """
    asyncpg `connect_args` that set session-level timeouts at connection startup.

    They are session defaults, so `set_local_statement_timeout` still raises
    or lowers the statement cap for one transaction. A timeout of 0 is left out
    rather than sent, which keeps the server's own setting (no limit by default).
    Values are whole milliseconds, rounded up so a sub-millisecond setting does
    not become 0 and switch the limit off.
    """
    settings = {
        name: str(math.ceil(seconds * 1000))
        for name, seconds in (
            ("statement_timeout", statement_timeout_seconds),
            (
                "idle_in_transaction_session_timeout",
                idle_in_transaction_timeout_seconds,
            ),
        )
        if seconds > 0
    }
    return {"server_settings": settings} if settings else {}


engine = create_async_engine(
    DATABASE_URL,
    echo=config.postgres.DB_ECHO,
    pool_size=config.postgres.DB_POOL_SIZE,
    max_overflow=config.postgres.DB_MAX_OVERFLOW,
    pool_timeout=POOL_TIMEOUT_SECONDS,
    pool_recycle=POOL_RECYCLE_SECONDS,
    pool_pre_ping=True,
    connect_args=session_timeouts(
        statement_timeout_seconds=config.postgres.DB_STATEMENT_TIMEOUT_SECONDS,
        idle_in_transaction_timeout_seconds=(
            config.postgres.DB_IDLE_IN_TRANSACTION_TIMEOUT_SECONDS
        ),
    ),
)

# Isolated pool for background-task workers. Defaults sum to 20 to match
# --max-async-tasks 20 on the worker command (infra/docker-compose.yml):
# even if every concurrent task grabs a session, nobody hits pool_timeout.
# Migrations build their own engine (migrations/env.py) with no limits at all.
tasks_engine = create_async_engine(
    DATABASE_URL,
    echo=config.postgres.DB_ECHO,
    pool_size=config.postgres.DB_TASKS_POOL_SIZE,
    max_overflow=config.postgres.DB_TASKS_MAX_OVERFLOW,
    pool_timeout=POOL_TIMEOUT_SECONDS,
    pool_recycle=POOL_RECYCLE_SECONDS,
    pool_pre_ping=True,
    connect_args=session_timeouts(
        idle_in_transaction_timeout_seconds=(
            config.postgres.DB_TASKS_IDLE_IN_TRANSACTION_TIMEOUT_SECONDS
        ),
    ),
)
