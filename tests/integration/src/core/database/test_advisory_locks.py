"""Advisory transaction locks between two real connections.

`pg_advisory_xact_lock` only means anything when a second backend is contending for the
same key, and only PostgreSQL releases it at COMMIT/ROLLBACK. Both halves are invisible to
a single-session or mocked test.
"""

import asyncio
from collections.abc import AsyncGenerator
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from src.core.outbox.repositories import OutboxRepository
from src.user.repositories import UserRepository

pytestmark = pytest.mark.asyncio(loop_scope="session")

# Long enough that a slow machine does not report a lock as blocking when it merely was
# not scheduled yet, short enough that a broken lock fails the suite instead of hanging it.
BLOCKED_TIMEOUT_SECONDS = 0.5
RELEASE_TIMEOUT_SECONDS = 10.0


@pytest_asyncio.fixture(loop_scope="session")
async def holder_session(
    integration_engine: AsyncEngine,
) -> AsyncGenerator[AsyncSession]:
    async with AsyncSession(integration_engine) as session:
        await session.begin()
        yield session
        await session.rollback()


@pytest_asyncio.fixture(loop_scope="session")
async def contender_session(
    integration_engine: AsyncEngine,
) -> AsyncGenerator[AsyncSession]:
    async with AsyncSession(integration_engine) as session:
        await session.begin()
        yield session
        await session.rollback()


async def test_try_lock_fails_while_held_and_succeeds_after_release(
    holder_session: AsyncSession, contender_session: AsyncSession
) -> None:
    repository = UserRepository()
    key = uuid4().hex

    assert await repository.try_xact_lock(holder_session, key) is True
    assert await repository.try_xact_lock(contender_session, key) is False

    # The lock lives on the transaction, so ending it is the only way to release it.
    await holder_session.rollback()

    assert await repository.try_xact_lock(contender_session, key) is True


async def test_blocking_lock_waits_for_the_holder_to_finish(
    holder_session: AsyncSession, contender_session: AsyncSession
) -> None:
    repository = UserRepository()
    key = uuid4().hex

    await repository.xact_lock(holder_session, key)
    waiter = asyncio.create_task(repository.xact_lock(contender_session, key))

    # shield keeps the acquisition alive when the wait times out, so the same attempt
    # can be awaited again below instead of being restarted after the release.
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(asyncio.shield(waiter), BLOCKED_TIMEOUT_SECONDS)
    assert not waiter.done()

    await holder_session.rollback()

    await asyncio.wait_for(waiter, RELEASE_TIMEOUT_SECONDS)
    assert waiter.done()


async def test_lock_keys_are_namespaced_per_model(
    holder_session: AsyncSession, contender_session: AsyncSession
) -> None:
    """The same key under two models must not contend.

    `_namespaced_lock_key` prefixes the table name before hashing, and only a real
    `pg_try_advisory_xact_lock` can show that the two prefixes land on different locks.
    """
    key = uuid4().hex

    assert await UserRepository().try_xact_lock(holder_session, key) is True
    assert await OutboxRepository().try_xact_lock(contender_session, key) is True
