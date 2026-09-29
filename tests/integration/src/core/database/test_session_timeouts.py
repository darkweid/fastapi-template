"""Session-level timeouts the app engine sends at connection startup.

Whether asyncpg's startup parameters reach the server, whether a transaction can still
override them, and whether the server really ends an idle transaction are all server
behaviour a fake session cannot show.
"""

import asyncio
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine

from src.core.database.engine import session_timeouts
from src.core.database.transactions import set_local_statement_timeout
from src.main.config import get_settings

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _engine(**timeouts: float) -> AsyncEngine:
    return create_async_engine(
        get_settings().postgres.dsn_async,
        connect_args={"statement_cache_size": 0, **session_timeouts(**timeouts)},
    )


@pytest_asyncio.fixture(loop_scope="session")
async def limited_engine(database_template: str) -> AsyncGenerator[AsyncEngine]:
    engine = _engine(
        statement_timeout_seconds=30, idle_in_transaction_timeout_seconds=60
    )
    yield engine
    await engine.dispose()


async def _show(session: AsyncSession, setting: str) -> str:
    return (await session.execute(text(f"SHOW {setting}"))).scalar_one()


async def test_connection_starts_with_the_configured_limits(
    limited_engine: AsyncEngine,
) -> None:
    async with AsyncSession(limited_engine) as session:
        statement_timeout = await _show(session, "statement_timeout")
        idle_timeout = await _show(session, "idle_in_transaction_session_timeout")
        await session.rollback()

    assert statement_timeout == "30s"
    assert idle_timeout == "1min"


async def test_a_transaction_still_sets_its_own_statement_cap(
    limited_engine: AsyncEngine,
) -> None:
    """A batch job relies on raising the cap above the session default."""
    async with limited_engine.connect() as connection:
        async with AsyncSession(bind=connection) as session:
            await session.begin()
            await set_local_statement_timeout(session, 120)
            inside = await _show(session, "statement_timeout")
            await session.rollback()
            after = await _show(session, "statement_timeout")
            await session.rollback()

    assert inside == "2min"
    assert after == "30s"


async def test_server_ends_a_transaction_left_idle_past_the_limit(
    database_template: str,
) -> None:
    engine = _engine(idle_in_transaction_timeout_seconds=0.2)
    try:
        async with AsyncSession(engine) as session:
            await session.execute(text("SELECT 1"))
            await asyncio.sleep(1)
            with pytest.raises(DBAPIError):
                await session.execute(text("SELECT 1"))
            await session.rollback()
    finally:
        await engine.dispose()
