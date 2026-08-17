from unittest.mock import AsyncMock, Mock

import pytest

from src.core.cache.memory_cache import InMemoryCache
from src.core.errors.exceptions import (
    InstanceProcessingException,
    TooManyRequestsException,
)
from src.user.auth.redis_keys import auth_redis_keys
import src.user.auth.usecases.login as login_usecase
from src.user.auth.usecases.login import (
    INVALID_CREDENTIALS_MESSAGE,
    LOGIN_FAILURES_LIMIT,
    LOGIN_FAILURES_WINDOW_SECONDS,
    LoginUserUseCase,
)
from src.user.models import User
from tests.factories.user_factory import build_user
from tests.fakes.db import FakeAsyncSession, FakeUnitOfWork
from tests.fakes.redis import InMemoryRedis


class FakeUserRepository:
    def __init__(self, user: User | None) -> None:
        self._user = user
        self.update = AsyncMock(return_value=user)

    async def get_single(
        self, session: FakeAsyncSession, **filters: object
    ) -> User | None:
        return self._user


def build_uow(user: User | None, session: FakeAsyncSession) -> FakeUnitOfWork:
    return FakeUnitOfWork(
        session=session,
        repositories={"users": FakeUserRepository(user)},
    )


@pytest.mark.asyncio
async def test_login_blocked_at_the_failure_limit_before_password_verify(
    monkeypatch: pytest.MonkeyPatch,
    fake_session: FakeAsyncSession,
    fake_redis: InMemoryRedis,
    cache: InMemoryCache,
) -> None:
    """
    Given: the per-email failure counter has reached the limit.
    When: another login for that email arrives.
    Then: it answers 429 with the window's remaining TTL, without spending a
    password hash verification or touching the database.
    """
    user = build_user()
    uow = build_uow(user, fake_session)
    verify_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(login_usecase, "verify_password", verify_mock)
    await fake_redis.setex(
        auth_redis_keys.login_failures("user@example.com"),
        600,
        str(LOGIN_FAILURES_LIMIT),
    )

    use_case = LoginUserUseCase(uow=uow, redis_client=fake_redis, cache=cache)

    with pytest.raises(TooManyRequestsException) as exc_info:
        await use_case.execute(
            login_usecase.LoginUserModel(
                email="user@example.com", password="plain-pass"
            )
        )

    assert exc_info.value.retry_after == 600
    verify_mock.assert_not_awaited()
    uow.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_wrong_password_increments_the_counter_and_arms_the_window(
    monkeypatch: pytest.MonkeyPatch,
    fake_session: FakeAsyncSession,
    fake_redis: InMemoryRedis,
    cache: InMemoryCache,
) -> None:
    user = build_user()
    uow = build_uow(user, fake_session)
    verify_mock = AsyncMock(return_value=False)
    monkeypatch.setattr(login_usecase, "verify_password", verify_mock)

    use_case = LoginUserUseCase(uow=uow, redis_client=fake_redis, cache=cache)

    with pytest.raises(InstanceProcessingException, match=INVALID_CREDENTIALS_MESSAGE):
        await use_case.execute(
            login_usecase.LoginUserModel(email="user@example.com", password="wrong")
        )

    failures_key = auth_redis_keys.login_failures("user@example.com")
    assert await fake_redis.get(failures_key) == "1"
    assert await fake_redis.ttl(failures_key) == LOGIN_FAILURES_WINDOW_SECONDS


@pytest.mark.asyncio
async def test_unknown_email_also_increments_the_counter(
    monkeypatch: pytest.MonkeyPatch,
    fake_session: FakeAsyncSession,
    fake_redis: InMemoryRedis,
    cache: InMemoryCache,
) -> None:
    # Counting unknown emails keeps the 429 from becoming an enumeration
    # oracle: a throttled answer never confirms the account exists.
    uow = build_uow(None, fake_session)
    verify_mock = AsyncMock(return_value=False)
    monkeypatch.setattr(login_usecase, "verify_password", verify_mock)

    use_case = LoginUserUseCase(uow=uow, redis_client=fake_redis, cache=cache)

    with pytest.raises(InstanceProcessingException, match=INVALID_CREDENTIALS_MESSAGE):
        await use_case.execute(
            login_usecase.LoginUserModel(email="missing@example.com", password="x1")
        )

    failures_key = auth_redis_keys.login_failures("missing@example.com")
    assert await fake_redis.get(failures_key) == "1"


@pytest.mark.asyncio
async def test_successful_login_clears_the_counter(
    monkeypatch: pytest.MonkeyPatch,
    fake_session: FakeAsyncSession,
    fake_redis: InMemoryRedis,
    cache: InMemoryCache,
) -> None:
    user = build_user()
    uow = build_uow(user, fake_session)
    monkeypatch.setattr(
        login_usecase, "needs_password_rehash", Mock(return_value=False)
    )
    monkeypatch.setattr(login_usecase, "verify_password", AsyncMock(return_value=True))
    monkeypatch.setattr(
        login_usecase, "create_access_token", AsyncMock(return_value="access")
    )
    monkeypatch.setattr(
        login_usecase, "create_refresh_token", AsyncMock(return_value="refresh")
    )
    failures_key = auth_redis_keys.login_failures("user@example.com")
    await fake_redis.setex(failures_key, 600, "3")

    use_case = LoginUserUseCase(uow=uow, redis_client=fake_redis, cache=cache)
    await use_case.execute(
        login_usecase.LoginUserModel(email="user@example.com", password="plain-pass")
    )

    assert await fake_redis.exists(failures_key) == 0


@pytest.mark.asyncio
async def test_admission_failure_with_correct_password_does_not_count(
    monkeypatch: pytest.MonkeyPatch,
    fake_session: FakeAsyncSession,
    fake_redis: InMemoryRedis,
    cache: InMemoryCache,
) -> None:
    # A correct password on a blocked or unverified account is the real owner
    # knocking, not credential stuffing - it must not feed the throttle.
    user = build_user(is_verified=False)
    uow = build_uow(user, fake_session)
    monkeypatch.setattr(login_usecase, "verify_password", AsyncMock(return_value=True))

    use_case = LoginUserUseCase(uow=uow, redis_client=fake_redis, cache=cache)

    with pytest.raises(InstanceProcessingException, match=INVALID_CREDENTIALS_MESSAGE):
        await use_case.execute(
            login_usecase.LoginUserModel(
                email="user@example.com", password="plain-pass"
            )
        )

    failures_key = auth_redis_keys.login_failures("user@example.com")
    assert await fake_redis.exists(failures_key) == 0
