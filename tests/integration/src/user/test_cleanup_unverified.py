"""The cleanup's SQL: which rows it takes, in which order, and past whose locks."""

import asyncio
from collections.abc import AsyncGenerator
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from src.core.cache.memory_cache import InMemoryCache
from src.core.utils.datetime_utils import get_utc_now
from src.event_log.models import EventLog
from src.user import tasks
from src.user.models import User
from src.user.repositories import UserRepository
from tests.factories.user_factory import build_user
from tests.fakes.redis import InMemoryRedis
from tests.helpers.races import WAIT_SECONDS

pytestmark = pytest.mark.asyncio(loop_scope="session")

# Own rows sit at the end of the id range, past anything another test left.
BEFORE_OWN_IDS = UUID(int=2**128 - 100)

Sessions = async_sessionmaker[AsyncSession]


@pytest.fixture
def sessions(integration_engine: AsyncEngine) -> Sessions:
    return async_sessionmaker(integration_engine, expire_on_commit=False)


def _user(**values: object) -> User:
    marker = uuid4().hex
    user = build_user(email=f"{marker}@example.com", username=marker, is_verified=False)
    user.created_at = func.now() - timedelta(days=30)
    for name, value in values.items():
        setattr(user, name, value)
    return user


@pytest_asyncio.fixture(loop_scope="session")
async def stale(sessions: Sessions) -> AsyncGenerator[list[UUID]]:
    """Three users the cleanup takes, ids ascending, and three it must leave."""
    expired = [_user(id=UUID(int=BEFORE_OWN_IDS.int + number)) for number in (3, 1, 2)]
    kept = [
        _user(is_verified=True),
        _user(created_at=func.now()),
        _user(is_deleted=True, deleted_at=func.now()),
    ]
    for number, user in enumerate(kept, start=4):
        user.id = UUID(int=BEFORE_OWN_IDS.int + number)
    async with sessions() as session:
        session.add_all([*expired, *kept])
        await session.commit()
    yield sorted(user.id for user in expired)
    ids = [user.id for user in [*expired, *kept]]
    async with sessions() as session:
        await session.execute(delete(EventLog).where(EventLog.object_id.in_(ids)))
        await session.execute(delete(User).where(User.id.in_(ids)))
        await session.commit()


async def _lock(
    session: AsyncSession, after_id: UUID = BEFORE_OWN_IDS, limit: int = 100
) -> list[UUID]:
    return await UserRepository().lock_unverified_ids(
        session,
        created_before=get_utc_now() - tasks.UNVERIFIED_USER_MAX_AGE,
        after_id=after_id,
        limit=limit,
    )


async def test_ids_are_walked_in_order_by_keyset(
    sessions: Sessions, stale: list[UUID]
) -> None:
    async with sessions() as session:
        assert await _lock(session) == stale
        assert await _lock(session, limit=2) == stale[:2]
        assert await _lock(session, after_id=stale[1]) == stale[2:]


async def test_a_row_someone_holds_is_skipped(
    sessions: Sessions, stale: list[UUID]
) -> None:
    """Waiting on it would stall the run behind one slow transaction."""
    async with sessions() as holder, sessions() as session:
        await holder.execute(select(User).where(User.id == stale[1]).with_for_update())

        async with asyncio.timeout(WAIT_SECONDS):
            assert await _lock(session) == [stale[0], stale[2]]


async def test_a_user_verified_after_the_read_is_not_deleted(
    sessions: Sessions, stale: list[UUID]
) -> None:
    async with sessions() as session:
        await session.execute(
            update(User).where(User.id == stale[0]).values(is_verified=True)
        )
        deleted = await UserRepository().soft_delete_unverified(
            session,
            stale,
            created_before=get_utc_now() - tasks.UNVERIFIED_USER_MAX_AGE,
        )

    assert sorted(user.id for user in deleted) == stale[1:]


async def test_the_task_deletes_every_batch_and_records_each_user(
    sessions: Sessions,
    stale: list[UUID],
    monkeypatch: pytest.MonkeyPatch,
    cache: InMemoryCache,
) -> None:
    monkeypatch.setattr(tasks, "CLEANUP_BATCH_SIZE", 2)

    async with sessions() as session:
        await tasks.cleanup_unverified_users(
            session=session, redis_client=InMemoryRedis(), cache=cache
        )

    async with sessions() as session:
        recorded = await session.scalars(
            select(EventLog.object_id).where(
                EventLog.event_type == "user.unverified_deleted",
                EventLog.object_id.in_(stale),
            )
        )
        assert sorted(recorded) == stale
        gone = await session.scalar(
            select(func.count())
            .select_from(User)
            .where(User.id.in_(stale), User.is_deleted, User.deleted_at.is_not(None))
        )
        assert gone == 3
