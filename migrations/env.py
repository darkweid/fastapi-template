import alembic_postgresql_enum  # noqa
import asyncio
import logging

from alembic import context
from alembic.config import Config
from sqlalchemy import MetaData
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
import models  # noqa

from src.core.database.base import Base
from src.main.config import config as app_config


config: Config = context.config
target_metadata: MetaData = Base.metadata
logger = logging.getLogger(__name__)


def include_object(object, name, type_, reflected, compare_to):
    if name in [
        "spatial_ref_sys",
    ]:
        return False
    return True


def _get_postgres_dsn() -> str:
    try:
        dsn = app_config.postgres.dsn_async
        return dsn
    except Exception as e:
        logger.error(f"An error occurred while getting DSN: {e}")
        raise


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    context.configure(
        url=_get_postgres_dsn(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """
    In this scenario we need to create an Engine
    and associate a connection with the context.
    """
    connectable: AsyncEngine = create_async_engine(url=_get_postgres_dsn())

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""

    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
