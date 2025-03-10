from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import sessionmaker

from .models import Base
from .settings import settings

DATABASE_URL = settings.build_postgres_dsn_async()

engine = create_async_engine(DATABASE_URL,
                             echo=settings.db_echo,
                             pool_size=15,
                             max_overflow=15,
                             pool_timeout=30,
                             pool_recycle=60 * 30,  # Restart the pool after 30 minutes
                             )

async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)  # noqa


from app.models import *  # noqa


async def init_models():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_session() -> AsyncSession:
    async with async_session() as session:
        yield session
