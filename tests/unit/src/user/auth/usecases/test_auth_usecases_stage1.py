from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import jwt
import pytest

from src.core.cache.memory_cache import InMemoryCache
from src.core.errors.exceptions import InstanceProcessingException
from src.core.schemas import SuccessResponse, TokenModel
from src.core.utils.security import build_email_throttle_key
from src.main.config import config
from src.user.auth.errors import UserBlockedError, UserNotVerifiedError
from src.user.auth.redis_keys import auth_redis_keys
from src.user.auth.schemas import (
    CreateUserModel,
    ResendVerificationModel,
    ResetPasswordModel,
    SendResetPasswordRequestModel,
)
from src.user.auth.usecases.get_access_by_refresh import GetTokensByRefreshUserUseCase
from src.user.auth.usecases.logout import LogoutUseCase
from src.user.auth.usecases.register import RegisterUseCase
from src.user.auth.usecases.resend_verification import SendVerificationUseCase
from src.user.auth.usecases.reset_password_confirm import ResetPasswordConfirmUseCase
from src.user.auth.usecases.reset_password_request import ResetPasswordRequestUseCase
from src.user.auth.usecases.verify_email import VerifyEmailUseCase
from src.user.cache_keys import user_cache_keys
from src.user.models import User
from src.user.schemas import UserProfileViewModel
from tests.factories.token_factory import (
    build_refresh_payload,
    build_reset_password_token,
    build_verification_token,
)
from tests.factories.user_factory import build_user
from tests.fakes.db import FakeAsyncSession, FakeUnitOfWork
from tests.fakes.redis import InMemoryRedis


class FakeUsersRepository:
    def __init__(
        self,
        user: User | None = None,
        updated_user: User | None = None,
    ) -> None:
        self._user = user
        self._updated_user = updated_user or user
        self.create = AsyncMock(side_effect=self._create)
        self.get_single = AsyncMock(side_effect=self._get_single)
        self.update = AsyncMock(side_effect=self._update)

    async def _create(self, session: FakeAsyncSession, data: dict) -> User:
        if self._user:
            return self._user
        return build_user(
            email=data["email"],
            username=data["username"],
            first_name=data["first_name"],
            last_name=data["last_name"],
            phone_number=data["phone_number"],
            password=data.get("password", "StrongPass1!"),
        )

    async def _get_single(
        self, session: FakeAsyncSession, **filters: object
    ) -> User | None:
        return self._user

    async def _update(
        self, session: FakeAsyncSession, data: dict, **filters: object
    ) -> User | None:
        return self._updated_user


class FakeEmailNotifier:
    def __init__(self) -> None:
        self.send = AsyncMock()
        self.release_throttle = AsyncMock()


def build_uow(
    session: FakeAsyncSession,
    users_repo: FakeUsersRepository,
) -> FakeUnitOfWork:
    return FakeUnitOfWork(session=session, repositories={"users": users_repo})


@pytest.mark.asyncio
async def test_get_tokens_by_refresh_user_usecase_success(
    fake_redis: InMemoryRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = build_user(is_verified=True, is_active=True)
    payload = build_refresh_payload(str(user.id))
    refresh_token = jwt.encode(
        payload,
        config.jwt.JWT_USER_SECRET_KEY,
        config.jwt.ALGORITHM,
    )
    access_token = "access-token"

    rotate_mock = AsyncMock(return_value=refresh_token)
    create_access_mock = AsyncMock(return_value=access_token)
    monkeypatch.setattr(
        "src.user.auth.usecases.get_access_by_refresh.rotate_refresh_token",
        rotate_mock,
    )
    monkeypatch.setattr(
        "src.user.auth.usecases.get_access_by_refresh.create_access_token",
        create_access_mock,
    )

    use_case = GetTokensByRefreshUserUseCase(redis_client=fake_redis)
    result = await use_case.execute(user=user, old_token_payload=payload)

    assert isinstance(result, TokenModel)
    assert result.refresh_token == refresh_token
    assert result.access_token == access_token
    rotate_mock.assert_awaited_once()
    create_access_mock.assert_awaited_once_with(
        {"sub": str(user.id)},
        redis_client=fake_redis,
        session_id=payload["session_id"],
    )


@pytest.mark.asyncio
async def test_get_tokens_by_refresh_user_usecase_blocked(
    fake_redis: InMemoryRedis,
) -> None:
    user = build_user(is_verified=True, is_active=False)
    payload = build_refresh_payload(str(user.id))

    use_case = GetTokensByRefreshUserUseCase(redis_client=fake_redis)

    with pytest.raises(UserBlockedError, match="User is blocked"):
        await use_case.execute(user=user, old_token_payload=payload)


@pytest.mark.asyncio
async def test_get_tokens_by_refresh_user_usecase_unverified(
    fake_redis: InMemoryRedis,
) -> None:
    user = build_user(is_verified=False, is_active=True)
    payload = build_refresh_payload(str(user.id))

    use_case = GetTokensByRefreshUserUseCase(redis_client=fake_redis)

    with pytest.raises(UserNotVerifiedError, match="User is not verified"):
        await use_case.execute(user=user, old_token_payload=payload)


@pytest.mark.asyncio
async def test_register_usecase_creates_user_and_sends_email(
    fake_session: FakeAsyncSession,
) -> None:
    user = build_user(email="john@example.com")
    users_repo = FakeUsersRepository(user=user)
    uow = build_uow(fake_session, users_repo)
    notifier = FakeEmailNotifier()
    use_case = RegisterUseCase(uow=uow, notifier=notifier)
    data = CreateUserModel(
        first_name="John",
        last_name="Doe",
        email="john@example.com",
        username="john_doe",
        phone_number="+1234567890",
        password="StrongPass1!",
    )

    call_order: list[str] = []
    notifier.send = AsyncMock(side_effect=lambda **_: call_order.append("notify"))
    uow.commit = AsyncMock(side_effect=lambda: call_order.append("commit"))

    result = await use_case.execute(data=data)

    assert isinstance(result, UserProfileViewModel)
    created_data = users_repo.create.await_args.kwargs["data"]
    assert created_data["password_hash"] != data.password
    notifier.send.assert_awaited_once()
    users_repo.create.assert_awaited_once()
    uow.flush.assert_not_awaited()
    uow.commit.assert_awaited_once()
    assert call_order == ["notify", "commit"]
    assert notifier.send.await_args.kwargs == {
        "uow": uow,
        "user": user,
    }


@pytest.mark.asyncio
async def test_register_usecase_propagates_notifier_failure(
    fake_session: FakeAsyncSession,
) -> None:
    user = build_user(email="john@example.com")
    users_repo = FakeUsersRepository(user=user)
    uow = build_uow(fake_session, users_repo)
    notifier = FakeEmailNotifier()
    notifier.send.side_effect = RuntimeError("outbox insert failed")
    use_case = RegisterUseCase(uow=uow, notifier=notifier)
    data = CreateUserModel(
        first_name="John",
        last_name="Doe",
        email="john@example.com",
        username="john_doe",
        phone_number="+1234567890",
        password="StrongPass1!",
    )

    with pytest.raises(RuntimeError, match="outbox insert failed"):
        await use_case.execute(data=data)

    notifier.send.assert_awaited_once()
    uow.commit.assert_not_awaited()
    uow.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_resend_verification_returns_success_on_missing_user(
    fake_session: FakeAsyncSession,
) -> None:
    users_repo = FakeUsersRepository(user=None)
    uow = build_uow(fake_session, users_repo)
    notifier = FakeEmailNotifier()
    use_case = SendVerificationUseCase(uow=uow, notifier=notifier)

    data = ResendVerificationModel(email="missing@example.com")
    result = await use_case.execute(data=data)

    assert result == SuccessResponse(success=True)
    notifier.send.assert_not_awaited()
    uow.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_resend_verification_success(
    fake_session: FakeAsyncSession,
) -> None:
    user = build_user(is_verified=False)
    users_repo = FakeUsersRepository(user=user)
    uow = build_uow(fake_session, users_repo)
    notifier = FakeEmailNotifier()
    use_case = SendVerificationUseCase(uow=uow, notifier=notifier)

    call_order: list[str] = []
    notifier.send = AsyncMock(side_effect=lambda **_: call_order.append("notify"))
    uow.commit = AsyncMock(side_effect=lambda: call_order.append("commit"))

    result = await use_case.execute(data=ResendVerificationModel(email=user.email))

    assert result == SuccessResponse(success=True)
    expected_throttle_key = build_email_throttle_key("resend_verification", user.email)
    notifier.send.assert_awaited_once_with(
        uow=uow, user=user, throttle_key=expected_throttle_key
    )
    uow.commit.assert_awaited_once()
    assert call_order == ["notify", "commit"]


@pytest.mark.asyncio
async def test_resend_verification_skips_if_throttled(
    fake_session: FakeAsyncSession,
) -> None:
    user = build_user(is_verified=False)
    users_repo = FakeUsersRepository(user=user)
    uow = build_uow(fake_session, users_repo)
    notifier = FakeEmailNotifier()
    notifier.send.side_effect = InstanceProcessingException("throttled")
    use_case = SendVerificationUseCase(uow=uow, notifier=notifier)

    data = ResendVerificationModel(email=user.email)
    result = await use_case.execute(data=data)

    assert result == SuccessResponse(success=True)
    notifier.send.assert_awaited_once()
    assert notifier.send.await_args.kwargs["uow"] is uow
    uow.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_resend_verification_releases_throttle_when_commit_fails(
    fake_session: FakeAsyncSession,
) -> None:
    user = build_user(is_verified=False)
    users_repo = FakeUsersRepository(user=user)
    uow = build_uow(fake_session, users_repo)
    notifier = FakeEmailNotifier()
    use_case = SendVerificationUseCase(uow=uow, notifier=notifier)
    uow.commit = AsyncMock(side_effect=RuntimeError("commit failed"))

    with pytest.raises(RuntimeError, match="commit failed"):
        await use_case.execute(
            data=ResendVerificationModel(email=user.email),
        )

    expected_throttle_key = build_email_throttle_key("resend_verification", user.email)
    notifier.release_throttle.assert_awaited_once_with(expected_throttle_key)


@pytest.mark.asyncio
async def test_resend_verification_user_already_verified(
    fake_session: FakeAsyncSession,
) -> None:
    user = build_user(is_verified=True)
    users_repo = FakeUsersRepository(user=user)
    uow = build_uow(fake_session, users_repo)
    notifier = FakeEmailNotifier()
    use_case = SendVerificationUseCase(uow=uow, notifier=notifier)

    data = ResendVerificationModel(email=user.email)
    result = await use_case.execute(data=data)

    assert result == SuccessResponse(success=True)
    notifier.send.assert_not_awaited()
    uow.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_reset_password_request_success(
    fake_session: FakeAsyncSession,
) -> None:
    user = build_user()
    users_repo = FakeUsersRepository(user=user)
    uow = build_uow(fake_session, users_repo)
    notifier = FakeEmailNotifier()
    use_case = ResetPasswordRequestUseCase(uow=uow, notifier=notifier)

    call_order: list[str] = []
    notifier.send = AsyncMock(side_effect=lambda **_: call_order.append("notify"))
    uow.commit = AsyncMock(side_effect=lambda: call_order.append("commit"))

    data = SendResetPasswordRequestModel(email=user.email)
    result = await use_case.execute(data=data)

    assert result == SuccessResponse(success=True)
    expected_throttle_key = build_email_throttle_key("password-reset", user.email)
    notifier.send.assert_awaited_once_with(
        uow=uow, user=user, throttle_key=expected_throttle_key
    )
    uow.commit.assert_awaited_once()
    assert call_order == ["notify", "commit"]


@pytest.mark.asyncio
async def test_reset_password_request_releases_throttle_when_commit_fails(
    fake_session: FakeAsyncSession,
) -> None:
    user = build_user()
    users_repo = FakeUsersRepository(user=user)
    uow = build_uow(fake_session, users_repo)
    notifier = FakeEmailNotifier()
    use_case = ResetPasswordRequestUseCase(uow=uow, notifier=notifier)
    uow.commit = AsyncMock(side_effect=RuntimeError("commit failed"))

    with pytest.raises(RuntimeError, match="commit failed"):
        await use_case.execute(
            data=SendResetPasswordRequestModel(email=user.email),
        )

    expected_throttle_key = build_email_throttle_key("password-reset", user.email)
    notifier.release_throttle.assert_awaited_once_with(expected_throttle_key)


@pytest.mark.asyncio
async def test_reset_password_request_skips_if_throttled(
    fake_session: FakeAsyncSession,
) -> None:
    user = build_user()
    users_repo = FakeUsersRepository(user=user)
    uow = build_uow(fake_session, users_repo)
    notifier = FakeEmailNotifier()
    notifier.send.side_effect = InstanceProcessingException("throttled")
    use_case = ResetPasswordRequestUseCase(uow=uow, notifier=notifier)

    data = SendResetPasswordRequestModel(email=user.email)
    result = await use_case.execute(data=data)

    assert result == SuccessResponse(success=True)
    notifier.send.assert_awaited_once()
    assert notifier.send.await_args.kwargs["uow"] is uow
    uow.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_reset_password_request_user_not_found(
    fake_session: FakeAsyncSession,
) -> None:
    users_repo = FakeUsersRepository(user=None)
    uow = build_uow(fake_session, users_repo)
    notifier = FakeEmailNotifier()
    use_case = ResetPasswordRequestUseCase(uow=uow, notifier=notifier)

    data = SendResetPasswordRequestModel(email="missing@example.com")
    result = await use_case.execute(data=data)

    assert result == SuccessResponse(success=True)
    notifier.send.assert_not_awaited()
    uow.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_logout_usecase_invalidates_current_session(
    fake_redis: InMemoryRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalidate_mock = AsyncMock()
    monkeypatch.setattr(
        "src.user.auth.usecases.logout.invalidate_user_session",
        invalidate_mock,
    )

    use_case = LogoutUseCase(redis_client=fake_redis)
    result = await use_case.execute(
        user_id="user-1",
        session_id="session-1",
    )

    assert result == SuccessResponse(success=True)
    invalidate_mock.assert_awaited_once_with(
        "user-1",
        "session-1",
        fake_redis,
    )


@pytest.mark.asyncio
async def test_logout_usecase_can_invalidate_all_sessions(
    fake_redis: InMemoryRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalidate_mock = AsyncMock()
    monkeypatch.setattr(
        "src.user.auth.usecases.logout.invalidate_all_user_sessions",
        invalidate_mock,
    )

    use_case = LogoutUseCase(redis_client=fake_redis)
    result = await use_case.execute(
        user_id="user-1",
        session_id="session-1",
        terminate_all_sessions=True,
    )

    assert result == SuccessResponse(success=True)
    invalidate_mock.assert_awaited_once_with("user-1", fake_redis)


@pytest.mark.asyncio
async def test_reset_password_confirm_success(
    fake_session: FakeAsyncSession,
    fake_redis: InMemoryRedis,
    monkeypatch: pytest.MonkeyPatch,
    cache: InMemoryCache,
) -> None:
    user = build_user()
    users_repo = FakeUsersRepository(user=user, updated_user=user)
    uow = build_uow(fake_session, users_repo)
    invalidate_mock = AsyncMock()
    monkeypatch.setattr(
        "src.user.auth.usecases.reset_password_confirm.invalidate_all_user_sessions",
        invalidate_mock,
    )

    token = await build_reset_password_token(
        {"email": user.email},
        fake_redis,
    )
    cache_key = user_cache_keys.summary(user.id)
    await cache.set(cache_key, {"name": "stale"}, ttl=60)
    cache_invalidate_spy = AsyncMock(wraps=cache.invalidate)
    cache.invalidate = cache_invalidate_spy  # type: ignore[method-assign]

    use_case = ResetPasswordConfirmUseCase(
        uow=uow, redis_client=fake_redis, cache=cache
    )
    result = await use_case.execute(
        data=ResetPasswordModel(token=token, password="StrongPass1!")
    )

    assert result == SuccessResponse(success=True)
    invalidate_mock.assert_awaited_once()
    uow.commit.assert_awaited_once()
    uow.flush.assert_awaited_once()
    assert await cache.get(cache_key) is None
    # Pre-commit bump plus the after-commit hook's second bump.
    assert cache_invalidate_spy.await_count == 2
    assert (
        await fake_redis.exists(
            auth_redis_keys.one_time_token("reset_password", user.email)
        )
        == 0
    )


@pytest.mark.asyncio
async def test_reset_password_confirm_invalid_mode(
    fake_session: FakeAsyncSession,
    fake_redis: InMemoryRedis,
    cache: InMemoryCache,
) -> None:
    users_repo = FakeUsersRepository(user=build_user())
    uow = build_uow(fake_session, users_repo)
    payload = {
        "email": "user@example.com",
        "mode": "other",
        "exp": int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()),
    }
    token = jwt.encode(
        payload, config.jwt.JWT_RESET_PASSWORD_SECRET_KEY, config.jwt.ALGORITHM
    )

    use_case = ResetPasswordConfirmUseCase(
        uow=uow, redis_client=fake_redis, cache=cache
    )
    result = await use_case.execute(
        data=ResetPasswordModel(token=token, password="StrongPass1!")
    )

    assert result == SuccessResponse(success=False)


@pytest.mark.asyncio
async def test_reset_password_confirm_rejects_inactive_jti(
    fake_session: FakeAsyncSession,
    fake_redis: InMemoryRedis,
    cache: InMemoryCache,
) -> None:
    user = build_user()
    users_repo = FakeUsersRepository(user=user, updated_user=user)
    uow = build_uow(fake_session, users_repo)
    token = await build_reset_password_token({"email": user.email}, fake_redis)
    await fake_redis.delete(
        auth_redis_keys.one_time_token("reset_password", user.email)
    )

    use_case = ResetPasswordConfirmUseCase(
        uow=uow, redis_client=fake_redis, cache=cache
    )
    result = await use_case.execute(
        data=ResetPasswordModel(token=token, password="StrongPass1!")
    )

    assert result == SuccessResponse(success=False)
    uow.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_reset_password_confirm_redis_failure_skips_commit(
    fake_session: FakeAsyncSession,
    fake_redis: InMemoryRedis,
    monkeypatch: pytest.MonkeyPatch,
    cache: InMemoryCache,
) -> None:
    user = build_user()
    users_repo = FakeUsersRepository(user=user, updated_user=user)
    uow = build_uow(fake_session, users_repo)
    invalidate_mock = AsyncMock(side_effect=RuntimeError("redis down"))
    monkeypatch.setattr(
        "src.user.auth.usecases.reset_password_confirm.invalidate_all_user_sessions",
        invalidate_mock,
    )
    token = await build_reset_password_token({"email": user.email}, fake_redis)
    use_case = ResetPasswordConfirmUseCase(
        uow=uow, redis_client=fake_redis, cache=cache
    )

    with pytest.raises(RuntimeError, match="redis down"):
        await use_case.execute(
            data=ResetPasswordModel(token=token, password="StrongPass1!")
        )

    uow.flush.assert_awaited_once()
    uow.commit.assert_not_awaited()
    uow.rollback.assert_awaited_once()
    assert (
        await fake_redis.exists(
            auth_redis_keys.one_time_token("reset_password", user.email)
        )
        == 0
    )


@pytest.mark.asyncio
async def test_reset_password_confirm_cannot_reuse_successful_token(
    fake_session: FakeAsyncSession,
    fake_redis: InMemoryRedis,
    monkeypatch: pytest.MonkeyPatch,
    cache: InMemoryCache,
) -> None:
    user = build_user()
    users_repo = FakeUsersRepository(user=user, updated_user=user)
    uow = build_uow(fake_session, users_repo)
    invalidate_mock = AsyncMock()
    monkeypatch.setattr(
        "src.user.auth.usecases.reset_password_confirm.invalidate_all_user_sessions",
        invalidate_mock,
    )
    token = await build_reset_password_token({"email": user.email}, fake_redis)
    use_case = ResetPasswordConfirmUseCase(
        uow=uow, redis_client=fake_redis, cache=cache
    )

    first_result = await use_case.execute(
        data=ResetPasswordModel(token=token, password="StrongPass1!")
    )
    second_result = await use_case.execute(
        data=ResetPasswordModel(token=token, password="StrongPass1!")
    )

    assert first_result == SuccessResponse(success=True)
    assert second_result == SuccessResponse(success=False)
    assert invalidate_mock.await_count == 1


@pytest.mark.asyncio
async def test_reset_password_confirm_commit_failure_after_invalidation_consumes_token(
    fake_session: FakeAsyncSession,
    fake_redis: InMemoryRedis,
    monkeypatch: pytest.MonkeyPatch,
    cache: InMemoryCache,
) -> None:
    user = build_user()
    users_repo = FakeUsersRepository(user=user, updated_user=user)
    uow = build_uow(fake_session, users_repo)
    uow.commit = AsyncMock(side_effect=RuntimeError("db down"))
    invalidate_mock = AsyncMock()
    monkeypatch.setattr(
        "src.user.auth.usecases.reset_password_confirm.invalidate_all_user_sessions",
        invalidate_mock,
    )
    token = await build_reset_password_token({"email": user.email}, fake_redis)
    use_case = ResetPasswordConfirmUseCase(
        uow=uow, redis_client=fake_redis, cache=cache
    )

    with pytest.raises(RuntimeError, match="db down"):
        await use_case.execute(
            data=ResetPasswordModel(token=token, password="StrongPass1!")
        )

    assert (
        await fake_redis.exists(
            auth_redis_keys.one_time_token("reset_password", user.email)
        )
        == 0
    )
    invalidate_mock.assert_awaited_once()
    uow.flush.assert_awaited_once()
    uow.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_verify_email_usecase_user_not_found(
    fake_session: FakeAsyncSession,
    fake_redis: InMemoryRedis,
    cache: InMemoryCache,
) -> None:
    users_repo = FakeUsersRepository(user=None)
    uow = build_uow(fake_session, users_repo)
    use_case = VerifyEmailUseCase(uow=uow, redis_client=fake_redis, cache=cache)

    token = await build_verification_token({"email": "missing@example.com"}, fake_redis)

    result = await use_case.execute(token)

    assert result == SuccessResponse(success=False)


@pytest.mark.asyncio
async def test_verify_email_usecase_already_verified(
    fake_session: FakeAsyncSession,
    fake_redis: InMemoryRedis,
    cache: InMemoryCache,
) -> None:
    user = build_user(is_verified=True)
    users_repo = FakeUsersRepository(user=user, updated_user=user)
    uow = build_uow(fake_session, users_repo)
    use_case = VerifyEmailUseCase(uow=uow, redis_client=fake_redis, cache=cache)

    token = await build_verification_token({"email": user.email}, fake_redis)

    result = await use_case.execute(token)

    assert result == SuccessResponse(success=True)
    assert (
        await fake_redis.exists(
            auth_redis_keys.one_time_token("verification", user.email)
        )
        == 0
    )


@pytest.mark.asyncio
async def test_verify_email_usecase_success(
    fake_session: FakeAsyncSession,
    fake_redis: InMemoryRedis,
    cache: InMemoryCache,
) -> None:
    user = build_user(is_verified=False)
    users_repo = FakeUsersRepository(user=user, updated_user=user)
    uow = build_uow(fake_session, users_repo)
    use_case = VerifyEmailUseCase(uow=uow, redis_client=fake_redis, cache=cache)
    cache_key = user_cache_keys.summary(user.id)
    await cache.set(cache_key, {"name": "stale"}, ttl=60)
    cache_invalidate_spy = AsyncMock(wraps=cache.invalidate)
    cache.invalidate = cache_invalidate_spy  # type: ignore[method-assign]

    token = await build_verification_token({"email": user.email}, fake_redis)

    result = await use_case.execute(token)

    assert result == SuccessResponse(success=True)
    uow.commit.assert_awaited_once()
    uow.flush.assert_not_awaited()
    # Pre-commit bump plus the after-commit hook's second bump.
    assert cache_invalidate_spy.await_count == 2
    assert (
        await fake_redis.exists(
            auth_redis_keys.one_time_token("verification", user.email)
        )
        == 0
    )
    assert await cache.get(cache_key) is None


@pytest.mark.asyncio
async def test_verify_email_usecase_invalid_token(
    fake_session: FakeAsyncSession,
    fake_redis: InMemoryRedis,
    cache: InMemoryCache,
) -> None:
    users_repo = FakeUsersRepository(user=None)
    uow = build_uow(fake_session, users_repo)
    use_case = VerifyEmailUseCase(uow=uow, redis_client=fake_redis, cache=cache)

    payload = {
        "email": "user@example.com",
        "exp": int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp()),
    }
    token = jwt.encode(payload, config.jwt.JWT_VERIFY_SECRET_KEY, config.jwt.ALGORITHM)

    result = await use_case.execute(token)

    assert result == SuccessResponse(success=False)


@pytest.mark.asyncio
async def test_verify_email_usecase_rejects_inactive_jti(
    fake_session: FakeAsyncSession,
    fake_redis: InMemoryRedis,
    cache: InMemoryCache,
) -> None:
    user = build_user(is_verified=False)
    users_repo = FakeUsersRepository(user=user, updated_user=user)
    uow = build_uow(fake_session, users_repo)
    use_case = VerifyEmailUseCase(uow=uow, redis_client=fake_redis, cache=cache)
    token = await build_verification_token({"email": user.email}, fake_redis)
    await fake_redis.delete(auth_redis_keys.one_time_token("verification", user.email))

    result = await use_case.execute(token)

    assert result == SuccessResponse(success=False)
    uow.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_verify_email_usecase_cannot_reuse_successful_token(
    fake_session: FakeAsyncSession,
    fake_redis: InMemoryRedis,
    cache: InMemoryCache,
) -> None:
    user = build_user(is_verified=False)
    users_repo = FakeUsersRepository(user=user, updated_user=user)
    uow = build_uow(fake_session, users_repo)
    use_case = VerifyEmailUseCase(uow=uow, redis_client=fake_redis, cache=cache)
    token = await build_verification_token({"email": user.email}, fake_redis)

    first_result = await use_case.execute(token)

    assert first_result == SuccessResponse(success=True)

    second_result = await use_case.execute(token)

    assert second_result == SuccessResponse(success=False)


@pytest.mark.asyncio
async def test_verify_email_usecase_commit_failure_keeps_token_active(
    fake_session: FakeAsyncSession,
    fake_redis: InMemoryRedis,
    cache: InMemoryCache,
) -> None:
    user = build_user(is_verified=False)
    users_repo = FakeUsersRepository(user=user, updated_user=user)
    uow = build_uow(fake_session, users_repo)
    uow.commit = AsyncMock(side_effect=RuntimeError("db down"))
    use_case = VerifyEmailUseCase(uow=uow, redis_client=fake_redis, cache=cache)
    token = await build_verification_token({"email": user.email}, fake_redis)

    with pytest.raises(RuntimeError, match="db down"):
        await use_case.execute(token)

    assert (
        await fake_redis.exists(
            auth_redis_keys.one_time_token("verification", user.email)
        )
        == 1
    )
    uow.flush.assert_not_awaited()
    uow.rollback.assert_awaited_once()
