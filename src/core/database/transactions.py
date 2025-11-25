from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession


@asynccontextmanager
async def maybe_begin(session: AsyncSession) -> AsyncGenerator[None]:
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
async def safe_begin(session: AsyncSession) -> AsyncGenerator[None]:
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
