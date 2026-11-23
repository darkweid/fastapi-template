from unittest.mock import AsyncMock, Mock

import pytest

from src.core.cache.memory_cache import InMemoryCache
from src.core.errors.exceptions import InstanceProcessingException
from src.core.schemas import TokenModel
from src.core.utils.security import DUMMY_PASSWORD_HASH
from src.event_log.enums import ActorType
from src.user.auth.realm import USER_AUTH_REALM
from src.user.auth.schemas import LoginUserModel
import src.user.auth.usecases.login as login_usecase
from src.user.auth.usecases.login import (
    INVALID_CREDENTIALS_MESSAGE,
    LoginUserUseCase,
)
from src.user.cache_keys import user_cache_keys
from src.user.models import User
from src.user.policies import AccountAccessViolation
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
async def test_login_rehashes_password_when_needed(
    monkeypatch: pytest.MonkeyPatch,
    fake_session: FakeAsyncSession,
    fake_redis: InMemoryRedis,
    cache: InMemoryCache,
) -> None:
    user = build_user()
    uow = build_uow(user, fake_session)
    users = uow.users

    needs_rehash_mock = Mock(return_value=True)
    hash_mock = AsyncMock(return_value="new-hash")
    verify_mock = AsyncMock(return_value=True)
    issue_session_pair_mock = AsyncMock(
        return_value=TokenModel(access_token="access", refresh_token="refresh")
    )

    monkeypatch.setattr(login_usecase, "needs_password_rehash", needs_rehash_mock)
    monkeypatch.setattr(login_usecase, "hash_password", hash_mock)
    monkeypatch.setattr(login_usecase, "verify_password", verify_mock)
    monkeypatch.setattr(login_usecase, "issue_session_pair", issue_session_pair_mock)
    cache_key = user_cache_keys.summary(user.id)
    await cache.set(cache_key, {"name": "stale"}, ttl=60)
    cache_invalidate_spy = AsyncMock(wraps=cache.invalidate)
    cache.invalidate = cache_invalidate_spy  # type: ignore[method-assign]

    use_case = LoginUserUseCase(uow=uow, redis_client=fake_redis, cache=cache)
    result = await use_case.execute(
        LoginUserModel(email="user@example.com", password="plain-pass")
    )

    assert result.access_token == "access"
    assert result.refresh_token == "refresh"
    needs_rehash_mock.assert_called_once_with(user.password_hash)
    verify_mock.assert_awaited_once_with("plain-pass", user.password_hash)
    hash_mock.assert_awaited_once_with("plain-pass")
    users.update.assert_awaited_once_with(
        fake_session,
        {"password_hash": "new-hash"},
        id=user.id,
    )
    uow.flush.assert_not_awaited()
    uow.commit.assert_awaited_once()
    assert await cache.get(cache_key) is None
    # Pre-commit bump plus the after-commit hook's second bump.
    assert cache_invalidate_spy.await_count == 2


@pytest.mark.asyncio
async def test_login_does_not_rehash_when_not_needed(
    monkeypatch: pytest.MonkeyPatch,
    fake_session: FakeAsyncSession,
    fake_redis: InMemoryRedis,
    cache: InMemoryCache,
) -> None:
    user = build_user()
    uow = build_uow(user, fake_session)
    users = uow.users

    needs_rehash_mock = Mock(return_value=False)
    verify_mock = AsyncMock(return_value=True)
    issue_session_pair_mock = AsyncMock(
        return_value=TokenModel(access_token="access", refresh_token="refresh")
    )

    monkeypatch.setattr(login_usecase, "needs_password_rehash", needs_rehash_mock)
    monkeypatch.setattr(login_usecase, "verify_password", verify_mock)
    monkeypatch.setattr(login_usecase, "issue_session_pair", issue_session_pair_mock)
    cache_key = user_cache_keys.summary(user.id)
    await cache.set(cache_key, {"name": "stale"}, ttl=60)

    use_case = LoginUserUseCase(uow=uow, redis_client=fake_redis, cache=cache)
    result = await use_case.execute(
        LoginUserModel(email="user@example.com", password="plain-pass")
    )

    assert result.access_token == "access"
    assert result.refresh_token == "refresh"
    needs_rehash_mock.assert_called_once_with(user.password_hash)
    verify_mock.assert_awaited_once_with("plain-pass", user.password_hash)
    users.update.assert_not_awaited()
    uow.flush.assert_not_awaited()
    uow.commit.assert_awaited_once()
    # Login invalidates unconditionally, even on the no-rehash path where the row
    # is not written - see LoginUserUseCase's docstring for why.
    assert await cache.get(cache_key) is None
    logged_actor, logged_event = uow.published[0]
    assert logged_event.code == "user.signed_in"
    assert logged_actor.actor_id == user.id


@pytest.mark.asyncio
async def test_login_returns_unified_error_for_missing_user_and_uses_dummy_hash(
    monkeypatch: pytest.MonkeyPatch,
    fake_session: FakeAsyncSession,
    fake_redis: InMemoryRedis,
    cache: InMemoryCache,
) -> None:
    uow = build_uow(None, fake_session)
    users = uow.users
    verify_mock = AsyncMock(return_value=False)
    debug_mock = Mock()
    monkeypatch.setattr(login_usecase, "verify_password", verify_mock)
    monkeypatch.setattr(login_usecase.logger, "debug", debug_mock)

    use_case = LoginUserUseCase(uow=uow, redis_client=fake_redis, cache=cache)

    with pytest.raises(InstanceProcessingException, match=INVALID_CREDENTIALS_MESSAGE):
        await use_case.execute(
            LoginUserModel(email="missing@example.com", password="plain-pass")
        )

    verify_mock.assert_awaited_once_with("plain-pass", DUMMY_PASSWORD_HASH)
    users.update.assert_not_awaited()
    uow.flush.assert_not_awaited()
    # The rejection commits on its own: the audit row is the only thing this
    # transaction carries, and it has to survive the error that follows it.
    uow.commit.assert_awaited_once()
    logged_actor, logged_event = uow.published[0]
    assert logged_actor.actor_type is ActorType.ANONYMOUS
    assert logged_event.code == "user.sign_in_failed"
    assert logged_event.reason == "unknown_email"
    debug_mock.assert_called_once()
    assert debug_mock.call_args.args[-1] == "unknown_email"


@pytest.mark.asyncio
async def test_login_returns_unified_error_for_wrong_password(
    monkeypatch: pytest.MonkeyPatch,
    fake_session: FakeAsyncSession,
    fake_redis: InMemoryRedis,
    cache: InMemoryCache,
) -> None:
    user = build_user()
    uow = build_uow(user, fake_session)
    users = uow.users
    verify_mock = AsyncMock(return_value=False)
    debug_mock = Mock()
    monkeypatch.setattr(login_usecase, "verify_password", verify_mock)
    monkeypatch.setattr(login_usecase.logger, "debug", debug_mock)

    use_case = LoginUserUseCase(uow=uow, redis_client=fake_redis, cache=cache)

    with pytest.raises(InstanceProcessingException, match=INVALID_CREDENTIALS_MESSAGE):
        await use_case.execute(
            LoginUserModel(email="user@example.com", password="wrong-pass")
        )

    verify_mock.assert_awaited_once_with("wrong-pass", user.password_hash)
    users.update.assert_not_awaited()
    uow.flush.assert_not_awaited()
    uow.commit.assert_awaited_once()
    logged_actor, logged_event = uow.published[0]
    # Anonymous although the account is known: the password did not match, so
    # nothing here proves the owner made the attempt.
    assert logged_actor.actor_type is ActorType.ANONYMOUS
    assert logged_event.reason == "wrong_password"
    debug_mock.assert_called_once()
    assert debug_mock.call_args.args[-1] == "wrong_password"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("user", "expected_violation"),
    [
        (build_user(is_verified=False), AccountAccessViolation.NOT_VERIFIED),
        (build_user(is_verified=True, is_active=False), AccountAccessViolation.BLOCKED),
    ],
)
async def test_login_returns_unified_error_for_account_state_failures(
    user: User,
    expected_violation: AccountAccessViolation,
    monkeypatch: pytest.MonkeyPatch,
    fake_session: FakeAsyncSession,
    fake_redis: InMemoryRedis,
    cache: InMemoryCache,
) -> None:
    uow = build_uow(user, fake_session)
    users = uow.users
    verify_mock = AsyncMock(return_value=True)
    debug_mock = Mock()
    monkeypatch.setattr(login_usecase, "verify_password", verify_mock)
    monkeypatch.setattr(login_usecase.logger, "debug", debug_mock)

    use_case = LoginUserUseCase(uow=uow, redis_client=fake_redis, cache=cache)

    with pytest.raises(InstanceProcessingException, match=INVALID_CREDENTIALS_MESSAGE):
        await use_case.execute(
            LoginUserModel(email="user@example.com", password="plain-pass")
        )

    verify_mock.assert_awaited_once_with("plain-pass", user.password_hash)
    users.update.assert_not_awaited()
    uow.flush.assert_not_awaited()
    uow.commit.assert_awaited_once()
    logged_actor, logged_event = uow.published[0]
    # The password matched, so this attempt is the account owner's.
    assert logged_actor.actor_id == user.id
    assert logged_event.reason == expected_violation
    debug_mock.assert_called_once_with(
        "[LoginUser] Account of '%s' fails admission (%s).",
        "us***@ex***",
        expected_violation,
    )


@pytest.mark.asyncio
async def test_a_session_that_cannot_be_issued_logs_no_sign_in(
    monkeypatch: pytest.MonkeyPatch,
    fake_session: FakeAsyncSession,
    fake_redis: InMemoryRedis,
    cache: InMemoryCache,
) -> None:
    """The caller gets an error and no credentials, so a durable row claiming
    the sign-in succeeded would be an audit trail lying about a Redis outage."""
    user = build_user()
    uow = build_uow(user, fake_session)
    monkeypatch.setattr(login_usecase, "verify_password", AsyncMock(return_value=True))
    monkeypatch.setattr(
        login_usecase, "needs_password_rehash", Mock(return_value=False)
    )
    monkeypatch.setattr(
        login_usecase,
        "issue_session_pair",
        AsyncMock(side_effect=ConnectionError("redis is down")),
    )

    use_case = LoginUserUseCase(uow=uow, redis_client=fake_redis, cache=cache)

    with pytest.raises(ConnectionError):
        await use_case.execute(
            LoginUserModel(email="user@example.com", password="plain-pass")
        )

    assert uow.published == []
    uow.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("locked", ["changed", "gone"])
async def test_a_password_changed_before_the_lock_refuses_the_sign_in(
    locked: str,
    monkeypatch: pytest.MonkeyPatch,
    fake_session: FakeAsyncSession,
    fake_redis: InMemoryRedis,
    cache: InMemoryCache,
) -> None:
    """The change has already wiped every session, so one issued now would
    outlive it."""
    user = build_user()
    changed = build_user(user_id=user.id, password="other")
    uow = build_uow(user, fake_session)
    users = uow.users
    get_single = AsyncMock(side_effect=[user, changed if locked == "changed" else None])
    users.get_single = get_single
    issue = AsyncMock()
    monkeypatch.setattr(login_usecase, "issue_session_pair", issue)

    with pytest.raises(InstanceProcessingException, match=INVALID_CREDENTIALS_MESSAGE):
        await LoginUserUseCase(uow=uow, redis_client=fake_redis, cache=cache).execute(
            LoginUserModel(email="user@example.com", password="password")
        )

    issue.assert_not_awaited()
    assert get_single.await_args_list[1].kwargs == {
        "for_update": True,
        "populate_existing": True,
        "id": user.id,
    }
    uow.commit.assert_awaited_once()
    assert [event.reason for _, event in uow.published] == ["wrong_password"]
    key = USER_AUTH_REALM.keys.login_failures("user@example.com")
    assert await fake_redis.get(key) == "1"


@pytest.mark.asyncio
async def test_a_concurrent_rehash_neither_refuses_nor_is_overwritten(
    monkeypatch: pytest.MonkeyPatch,
    fake_session: FakeAsyncSession,
    fake_redis: InMemoryRedis,
    cache: InMemoryCache,
) -> None:
    user = build_user()
    rehashed = build_user(user_id=user.id)
    uow = build_uow(user, fake_session)
    users = uow.users
    users.get_single = AsyncMock(side_effect=[user, rehashed])
    monkeypatch.setattr(login_usecase, "needs_password_rehash", Mock(return_value=True))

    await LoginUserUseCase(uow=uow, redis_client=fake_redis, cache=cache).execute(
        LoginUserModel(email="user@example.com", password="password")
    )

    users.update.assert_not_awaited()
    assert uow.published_codes == ["user.signed_in"]


@pytest.mark.asyncio
async def test_an_account_failing_admission_is_never_rehashed(
    monkeypatch: pytest.MonkeyPatch,
    fake_session: FakeAsyncSession,
    fake_redis: InMemoryRedis,
    cache: InMemoryCache,
) -> None:
    """The extra Argon2 call would confirm the password by timing."""
    user = build_user(is_active=False)
    hash_mock = AsyncMock()
    monkeypatch.setattr(login_usecase, "needs_password_rehash", Mock(return_value=True))
    monkeypatch.setattr(login_usecase, "hash_password", hash_mock)

    with pytest.raises(InstanceProcessingException):
        await LoginUserUseCase(
            uow=build_uow(user, fake_session), redis_client=fake_redis, cache=cache
        ).execute(LoginUserModel(email="user@example.com", password="password"))

    hash_mock.assert_not_awaited()
