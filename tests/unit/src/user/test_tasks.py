from datetime import datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.exc import DBAPIError

from src.core.auth.challenges import ActiveChallengeRegistry
from src.core.cache.memory_cache import InMemoryCache
from src.core.database.filters import FilterCondition
from src.core.utils.datetime_utils import get_utc_now
from src.event_log.actor import Actor
from src.user.auth.realm import (
    RESET_PASSWORD_PURPOSE,
    USER_AUTH_REALM,
    VERIFICATION_PURPOSE,
)
from src.user.cache_keys import user_cache_keys
from src.user.dto import UserIdentity
from src.user.repositories import UserRepository
from src.user.tasks import UNVERIFIED_USER_MAX_AGE, cleanup_unverified_users
from tests.fakes.db import FakeAsyncSession, FakeUnitOfWork
from tests.fakes.redis import InMemoryRedis
from tests.helpers.providers import ProvideValue

NOW = get_utc_now()


def _ids(count: int) -> list[UUID]:
    return sorted(uuid4() for _ in range(count))


class FakeUsersRepository:
    """Hands out `batches` one by one; a batch listed in `failing` cannot be
    deleted."""

    def __init__(
        self, *batches: list[UUID], failing: tuple[list[UUID], ...] = ()
    ) -> None:
        self._failing = failing
        self.lock_unverified_ids = AsyncMock(side_effect=[*batches, []])
        self.soft_delete_unverified = AsyncMock(side_effect=self._delete)

    async def _delete(
        self, session: FakeAsyncSession, ids: list[UUID], *, created_before: datetime
    ) -> list[UserIdentity]:
        if ids in self._failing:
            raise DBAPIError(
                "UPDATE", None, ValueError("Key (email)=(leaked@example.com)")
            )
        return [UserIdentity(id=user_id, email=f"{user_id}@e.com") for user_id in ids]


class CleanupRun:
    """The task over fakes; every batch gets a unit of work of its own."""

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        users: FakeUsersRepository,
        redis: InMemoryRedis,
        cache: InMemoryCache,
    ) -> None:
        self.units: list[FakeUnitOfWork] = []
        self.users = users
        self.redis = redis
        self.cache = cache
        self.session = FakeAsyncSession()
        self.captured = MagicMock()
        monkeypatch.setattr("src.user.tasks.ApplicationUnitOfWork", self._unit)
        monkeypatch.setattr("src.user.tasks.get_utc_now", ProvideValue(NOW))
        monkeypatch.setattr(
            "src.user.tasks.sentry_sdk.capture_exception", self.captured
        )

    def _unit(self, session: FakeAsyncSession) -> FakeUnitOfWork:
        unit = FakeUnitOfWork(session=session, repositories={"users": self.users})
        self.units.append(unit)
        return unit

    async def __call__(self) -> str:
        return await cleanup_unverified_users(
            session=self.session, redis_client=self.redis, cache=self.cache
        )

    @property
    def deleted_ids(self) -> list[UUID]:
        return [
            event.object_id
            for unit in self.units
            if unit.commit.await_count
            for actor, event in unit.published
            if actor == Actor.system() and event.code == "user.unverified_deleted"
        ]


async def test_cleanup_walks_batches_past_the_last_id(
    monkeypatch: pytest.MonkeyPatch, fake_redis: InMemoryRedis, cache: InMemoryCache
) -> None:
    first, second = _ids(2), _ids(1)
    users = FakeUsersRepository(first, second)
    run = CleanupRun(monkeypatch, users, fake_redis, cache)

    assert await run() == "Deleted 3 unverified users."

    walked = [call.kwargs for call in users.lock_unverified_ids.await_args_list]
    assert [kwargs["after_id"] for kwargs in walked] == [None, first[-1], second[-1]]
    assert {kwargs["created_before"] for kwargs in walked} == {
        NOW - UNVERIFIED_USER_MAX_AGE
    }
    assert [unit.commit.await_count for unit in run.units] == [1, 1, 0]
    assert run.deleted_ids == [*first, *second]
    run.captured.assert_not_called()


async def test_cleanup_retires_challenges_and_bumps_the_cache_after_the_commit(
    monkeypatch: pytest.MonkeyPatch, fake_redis: InMemoryRedis, cache: InMemoryCache
) -> None:
    (user_id,) = ids = _ids(1)
    email = f"{user_id}@e.com"
    registry = ActiveChallengeRegistry(USER_AUTH_REALM)
    for purpose in (VERIFICATION_PURPOSE, RESET_PASSWORD_PURPOSE):
        await registry.store(purpose, email, "jti", 60, fake_redis)
        await registry.store(purpose, "kept@e.com", "jti", 60, fake_redis)
    key = user_cache_keys.summary(user_id)
    await cache.set(key, {"id": "stale"})
    run = CleanupRun(monkeypatch, FakeUsersRepository(ids), fake_redis, cache)

    await run()

    assert await cache.get(key) is None
    for purpose in (VERIFICATION_PURPOSE, RESET_PASSWORD_PURPOSE):
        keys = USER_AUTH_REALM.keys
        assert await fake_redis.exists(keys.one_time(purpose, email)) == 0
        assert await fake_redis.exists(keys.one_time(purpose, "kept@e.com")) == 1


async def test_cleanup_keeps_the_cache_when_the_batch_rolls_back(
    monkeypatch: pytest.MonkeyPatch, fake_redis: InMemoryRedis, cache: InMemoryCache
) -> None:
    ids = _ids(1)
    key = user_cache_keys.summary(ids[0])
    await cache.set(key, {"id": "live"})
    users = FakeUsersRepository(ids)
    run = CleanupRun(monkeypatch, users, fake_redis, cache)
    run.session.commit.side_effect = RuntimeError("connection lost")

    assert await run() == "Deleted 0 unverified users."

    assert await cache.get(key) == {"id": "live"}


async def test_a_failing_cleanup_batch_is_reported_and_passed_over(
    monkeypatch: pytest.MonkeyPatch, fake_redis: InMemoryRedis, cache: InMemoryCache
) -> None:
    """One row nothing can delete must neither loop the task nor keep the users
    after it."""
    bad, good = _ids(2), _ids(2)
    users = FakeUsersRepository(bad, good, failing=(bad,))
    run = CleanupRun(monkeypatch, users, fake_redis, cache)

    assert await run() == "Deleted 2 unverified users."

    assert run.deleted_ids == good
    run.units[0].rollback.assert_awaited_once()
    run.captured.assert_called_once()
    (reported,) = run.captured.call_args.args
    assert str(reported).endswith(": DBAPIError driver=ValueError")
    assert reported.__cause__ is None and reported.__context__ is None


async def test_cleanup_ends_when_the_ids_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch, fake_redis: InMemoryRedis, cache: InMemoryCache
) -> None:
    users = FakeUsersRepository()
    users.lock_unverified_ids = AsyncMock(side_effect=RuntimeError("database down"))
    run = CleanupRun(monkeypatch, users, fake_redis, cache)

    assert await run() == "Deleted 0 unverified users."

    users.lock_unverified_ids.assert_awaited_once()
    run.captured.assert_called_once()


async def test_a_redis_outage_ends_the_cleanup_and_is_reported_once(
    monkeypatch: pytest.MonkeyPatch, fake_redis: InMemoryRedis, cache: InMemoryCache
) -> None:
    """Every further batch would fail the same way, one Sentry event each."""
    users = FakeUsersRepository(_ids(1), _ids(1))
    run = CleanupRun(monkeypatch, users, fake_redis, cache)
    monkeypatch.setattr(
        fake_redis, "delete", AsyncMock(side_effect=RedisConnectionError("down"))
    )

    assert await run() == "Deleted 0 unverified users."

    users.lock_unverified_ids.assert_awaited_once()
    run.units[0].rollback.assert_awaited_once()
    run.captured.assert_called_once()


def test_cleanup_task_registration() -> None:
    assert cleanup_unverified_users.task_name == "cleanup_unverified_users"
    assert cleanup_unverified_users.labels["schedule"] == [{"cron": "0 */10 * * *"}]


@pytest.mark.asyncio
async def test_batch_soft_delete_raises_value_error_on_empty_filters(
    fake_session: FakeAsyncSession,
) -> None:
    repo = UserRepository()

    with pytest.raises(
        ValueError, match="At least one filter condition must be provided"
    ):
        await repo.batch_soft_delete(fake_session, filters=FilterCondition())
