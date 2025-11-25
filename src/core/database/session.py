from collections.abc import AsyncGenerator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.core.database.engine import engine
from src.core.database.uow import ApplicationUnitOfWork, RepositoryProtocol, get_uow

async_session = async_sessionmaker(bind=engine, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession]:
    async with async_session() as session:
        yield session


async def get_unit_of_work(
    session: AsyncSession = Depends(get_session),
) -> AsyncGenerator[ApplicationUnitOfWork[RepositoryProtocol]]:
    """
    Dependency injection function that provides a Unit of Work instance.

    This function creates a new ApplicationUnitOfWork for each request
    and yields it to the caller. This is intended to be used as a FastAPI
    dependency in route handlers.

    Args:
        session: SQLAlchemy AsyncSession, injected automatically from get_session

    Yields:
        ApplicationUnitOfWork: A Unit of Work instance for transaction management
    """
    uow = await get_uow(session)
    yield uow
