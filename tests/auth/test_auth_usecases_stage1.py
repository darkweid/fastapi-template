from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import jwt
import pytest
from starlette.datastructures import URL

from src.core.errors.exceptions import (
    InstanceProcessingException,
    PermissionDeniedException,
    UnauthorizedException,
)
from src.core.schemas import SuccessResponse, TokenModel
from src.core.utils.security import build_email_throttle_key
from src.main.config import config
from src.user.auth.schemas import (
    CreateUserModel,
    ResendVerificationModel,
    ResetPasswordModel,
    SendResetPasswordRequestModel,
)
from src.user.auth.usecases.get_access_by_refresh import GetTokensByRefreshUserUseCase
from src.user.auth.usecases.register import RegisterUseCase
from src.user.auth.usecases.resend_verification import SendVerificationUseCase
from src.user.auth.usecases.reset_password_confirm import ResetPasswordConfirmUseCase
from src.user.auth.usecases.reset_password_request import ResetPasswordRequestUseCase
from src.user.auth.usecases.verify_email import VerifyEmailUseCase
from src.user.models import User
from src.user.schemas import UserProfileViewModel
from tests.factories.token_factory import build_refresh_payload
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


class FakeVerificationNotifier:
    def __init__(self) -> None:
        self.send_verification = AsyncMock()


class FakeResetPasswordNotifier:
    def __init__(self) -> None:
        self.send_password_reset_email = AsyncMock()


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
    create_access_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_tokens_by_refresh_user_usecase_blocked(
    fake_redis: InMemoryRedis,
) -> None:
    user = build_user(is_verified=True, is_active=False)
    payload = build_refresh_payload(str(user.id))

    use_case = GetTokensByRefreshUserUseCase(redis_client=fake_redis)

    with pytest.raises(PermissionDeniedException, match="User is blocked"):
        await use_case.execute(user=user, old_token_payload=payload)


@pytest.mark.asyncio
async def test_get_tokens_by_refresh_user_usecase_unverified(
    fake_redis: InMemoryRedis,
) -> None:
    user = build_user(is_verified=False, is_active=True)
    payload = build_refresh_payload(str(user.id))

    use_case = GetTokensByRefreshUserUseCase(redis_client=fake_redis)

    with pytest.raises(InstanceProcessingException, match="User is not verified"):
        await use_case.execute(user=user, old_token_payload=payload)


@pytest.mark.asyncio
async def test_register_usecase_creates_user_and_sends_email(
    fake_session: FakeAsyncSession,
) -> None:
    user = build_user(email="john@example.com")
    users_repo = FakeUsersRepository(user=user)
    uow = build_uow(fake_session, users_repo)
    notifier = FakeVerificationNotifier()
    use_case = RegisterUseCase(uow=uow, notifier=notifier)
    data = CreateUserModel(
        first_name="John",
        last_name="Doe",
        email="john@example.com",
        username="john_doe",
        phone_number="+1234567890",
        password="StrongPass1!",
    )
    base_url = URL("http://testserver/")

    result = await use_case.execute(data=data, request_base_url=base_url)

    assert isinstance(result, UserProfileViewModel)
    created_data = users_repo.create.await_args.kwargs["data"]
    assert created_data["password_hash"] != data.password
    throttle_key = build_email_throttle_key("signup", user.email)
    notifier.send_verification.assert_awaited_once()
    users_repo.create.assert_awaited_once()
    uow.commit.assert_awaited_once()
    assert notifier.send_verification.await_args.kwargs["throttle_key"] == throttle_key


@pytest.mark.asyncio
async def test_resend_verification_returns_success_on_missing_user(
    fake_session: FakeAsyncSession,
) -> None:
    users_repo = FakeUsersRepository(user=None)
    uow = build_uow(fake_session, users_repo)
    notifier = FakeVerificationNotifier()
    use_case = SendVerificationUseCase(uow=uow, notifier=notifier)

    data = ResendVerificationModel(email="missing@example.com")
    result = await use_case.execute(data=data, request_base_url=URL("http://x/"))

    assert result == SuccessResponse(success=True)
    notifier.send_verification.assert_not_awaited()


@pytest.mark.asyncio
async def test_resend_verification_skips_if_throttled(
    fake_session: FakeAsyncSession,
) -> None:
    user = build_user(is_verified=False)
    users_repo = FakeUsersRepository(user=user)
    uow = build_uow(fake_session, users_repo)
    notifier = FakeVerificationNotifier()
    notifier.send_verification.side_effect = InstanceProcessingException("throttled")
    use_case = SendVerificationUseCase(uow=uow, notifier=notifier)

    data = ResendVerificationModel(email=user.email)
    result = await use_case.execute(data=data, request_base_url=URL("http://x/"))

    assert result == SuccessResponse(success=True)
    notifier.send_verification.assert_awaited_once()


@pytest.mark.asyncio
async def test_resend_verification_user_already_verified(
    fake_session: FakeAsyncSession,
) -> None:
    user = build_user(is_verified=True)
    users_repo = FakeUsersRepository(user=user)
    uow = build_uow(fake_session, users_repo)
    notifier = FakeVerificationNotifier()
    use_case = SendVerificationUseCase(uow=uow, notifier=notifier)

    data = ResendVerificationModel(email=user.email)
    result = await use_case.execute(data=data, request_base_url=URL("http://x/"))

    assert result == SuccessResponse(success=True)
    notifier.send_verification.assert_not_awaited()


@pytest.mark.asyncio
async def test_reset_password_request_success(
    fake_session: FakeAsyncSession,
) -> None:
    user = build_user()
    users_repo = FakeUsersRepository(user=user)
    uow = build_uow(fake_session, users_repo)
    notifier = FakeResetPasswordNotifier()
    use_case = ResetPasswordRequestUseCase(uow=uow, notifier=notifier)

    data = SendResetPasswordRequestModel(email=user.email)
    result = await use_case.execute(data=data, request_base_url=URL("http://x/"))

    assert result == SuccessResponse(success=True)
    notifier.send_password_reset_email.assert_awaited_once()


@pytest.mark.asyncio
async def test_reset_password_request_user_not_found(
    fake_session: FakeAsyncSession,
) -> None:
    users_repo = FakeUsersRepository(user=None)
    uow = build_uow(fake_session, users_repo)
    notifier = FakeResetPasswordNotifier()
    use_case = ResetPasswordRequestUseCase(uow=uow, notifier=notifier)

    data = SendResetPasswordRequestModel(email="missing@example.com")
    result = await use_case.execute(data=data, request_base_url=URL("http://x/"))

    assert result == SuccessResponse(success=True)
    notifier.send_password_reset_email.assert_not_awaited()


@pytest.mark.asyncio
async def test_reset_password_confirm_success(
    fake_session: FakeAsyncSession,
    fake_redis: InMemoryRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = build_user()
    users_repo = FakeUsersRepository(user=user, updated_user=user)
    uow = build_uow(fake_session, users_repo)
    invalidate_mock = AsyncMock()
    monkeypatch.setattr(
        "src.user.auth.usecases.reset_password_confirm.invalidate_all_user_sessions",
        invalidate_mock,
    )

    payload = {
        "email": user.email,
        "mode": "reset_password_token",
        "exp": int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()),
    }
    token = jwt.encode(
        payload, config.jwt.JWT_RESET_PASSWORD_SECRET_KEY, config.jwt.ALGORITHM
    )

    use_case = ResetPasswordConfirmUseCase(uow=uow, redis_client=fake_redis)
    result = await use_case.execute(
        data=ResetPasswordModel(token=token, password="StrongPass1!")
    )

    assert result == SuccessResponse(success=True)
    invalidate_mock.assert_awaited_once()
    uow.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_reset_password_confirm_invalid_mode(
    fake_session: FakeAsyncSession,
    fake_redis: InMemoryRedis,
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

    use_case = ResetPasswordConfirmUseCase(uow=uow, redis_client=fake_redis)
    result = await use_case.execute(
        data=ResetPasswordModel(token=token, password="StrongPass1!")
    )

    assert result == SuccessResponse(success=False)


@pytest.mark.asyncio
async def test_verify_email_usecase_user_not_found(
    fake_session: FakeAsyncSession,
) -> None:
    users_repo = FakeUsersRepository(user=None)
    uow = build_uow(fake_session, users_repo)
    use_case = VerifyEmailUseCase(uow=uow)

    payload = {
        "email": "missing@example.com",
        "exp": int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()),
    }
    token = jwt.encode(payload, config.jwt.JWT_VERIFY_SECRET_KEY, config.jwt.ALGORITHM)

    result = await use_case.execute(token)

    assert result == SuccessResponse(success=False)


@pytest.mark.asyncio
async def test_verify_email_usecase_already_verified(
    fake_session: FakeAsyncSession,
) -> None:
    user = build_user(is_verified=True)
    users_repo = FakeUsersRepository(user=user, updated_user=user)
    uow = build_uow(fake_session, users_repo)
    use_case = VerifyEmailUseCase(uow=uow)

    payload = {
        "email": user.email,
        "exp": int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()),
    }
    token = jwt.encode(payload, config.jwt.JWT_VERIFY_SECRET_KEY, config.jwt.ALGORITHM)

    result = await use_case.execute(token)

    assert result == SuccessResponse(success=True)


@pytest.mark.asyncio
async def test_verify_email_usecase_success(
    fake_session: FakeAsyncSession,
) -> None:
    user = build_user(is_verified=False)
    users_repo = FakeUsersRepository(user=user, updated_user=user)
    uow = build_uow(fake_session, users_repo)
    use_case = VerifyEmailUseCase(uow=uow)

    payload = {
        "email": user.email,
        "exp": int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp()),
    }
    token = jwt.encode(payload, config.jwt.JWT_VERIFY_SECRET_KEY, config.jwt.ALGORITHM)

    result = await use_case.execute(token)

    assert result == SuccessResponse(success=True)


@pytest.mark.asyncio
async def test_verify_email_usecase_invalid_token(
    fake_session: FakeAsyncSession,
) -> None:
    users_repo = FakeUsersRepository(user=None)
    uow = build_uow(fake_session, users_repo)
    use_case = VerifyEmailUseCase(uow=uow)

    payload = {
        "email": "user@example.com",
        "exp": int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp()),
    }
    token = jwt.encode(payload, config.jwt.JWT_VERIFY_SECRET_KEY, config.jwt.ALGORITHM)

    with pytest.raises(UnauthorizedException):
        await use_case.execute(token)
