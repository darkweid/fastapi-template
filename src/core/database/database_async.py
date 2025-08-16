from contextlib import asynccontextmanager
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import sessionmaker

from src.core.settings import settings
from .models import Base

DATABASE_URL = settings.build_postgres_dsn_async()

engine = create_async_engine(DATABASE_URL,
                             echo=settings.db_echo,
                             pool_size=10,
                             max_overflow=10,
                             pool_timeout=30,
                             pool_recycle=60 * 30,  # Restart the pool after 30 minutes
                             )

async_session = sessionmaker(bind=engine,  # type: ignore
                             class_=AsyncSession,
                             expire_on_commit=False)


async def init_models():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        yield session


@asynccontextmanager
async def maybe_begin(session: AsyncSession) -> AsyncGenerator[None, None]:
    """
    Context manager that ensures an AsyncSession transaction is active.

    If the session is already in a transaction, yields immediately without
    starting a new one. Otherwise, begins a new transaction and automatically
    commits on successful exit or rolls back on exception.

    Args:
        session (AsyncSession): The SQLAlchemy async session to manage.
    """
    if session.in_transaction():
        yield
    else:
        async with session.begin():
            yield


@asynccontextmanager
async def safe_begin(session: AsyncSession) -> AsyncGenerator[None, None]:
    """
    Context manager that guarantees a transactional scope for ORM operations.

    - If the session is not already in a transaction, opens a regular transaction
      (BEGIN...COMMIT/ROLLBACK).
    - If the session is already in a transaction, creates a nested transaction
      (SAVEPOINT) to allow local commit or rollback without affecting the outer transaction.

    Args:
        session (AsyncSession): The SQLAlchemy async session to manage.
    """
    if session.in_transaction():
        async with session.begin_nested():
            yield
    else:
        async with session.begin():
            yield
