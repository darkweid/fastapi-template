"""Transaction-local statement timeout against a real PostgreSQL.

Whether the setting reverts at the end of the transaction, and whether the server really
cancels a statement over it, are both server behaviour that a fake session cannot show.
"""

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

from src.core.database.transactions import set_local_statement_timeout

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest_asyncio.fixture(loop_scope="session")
async def connection(
    integration_engine: AsyncEngine,
) -> AsyncGenerator[AsyncConnection]:
    # One connection for the whole test, so "after the transaction" reads the same
    # backend the setting was applied on rather than whichever one the pool hands out.
    async with integration_engine.connect() as connection:
        yield connection


async def _statement_timeout(session: AsyncSession) -> str:
    return (await session.execute(text("SHOW statement_timeout"))).scalar_one()


async def test_timeout_applies_inside_the_transaction_only(
    connection: AsyncConnection,
) -> None:
    async with AsyncSession(bind=connection) as session:
        before = await _statement_timeout(session)
        await session.rollback()

        await session.begin()
        await set_local_statement_timeout(session, 1.5)
        inside = await _statement_timeout(session)
        await session.commit()

        after = await _statement_timeout(session)
        await session.rollback()

    assert inside == "1500ms"
    assert after == before
    assert before != inside


async def test_statement_over_the_timeout_is_cancelled(
    connection: AsyncConnection,
) -> None:
    async with AsyncSession(bind=connection) as session:
        await session.begin()
        await set_local_statement_timeout(session, 0.05)

        with pytest.raises(DBAPIError) as caught:
            await session.execute(text("SELECT pg_sleep(5)"))
        await session.rollback()

    assert getattr(caught.value.orig, "sqlstate", None) == "57014"
