from unittest.mock import AsyncMock

import pytest

from scripts.app import create_admin
from src.core.cache.memory_cache import InMemoryCache
from src.core.schemas import TokenModel
from src.core.utils import security
from src.event_log.actor import Actor
from src.user.auth.schemas import (
    CreateUserModel,
    LoginUserModel,
    ResetPasswordModel,
    UserNewPassword,
)
from src.user.auth.usecases import login, register, reset_password_confirm
from src.user.usecases import update_password
from tests.factories.user_factory import build_user
from tests.fakes.db import FakeAsyncSession, FakeUnitOfWork
from tests.fakes.redis import InMemoryRedis

OLD = "CurrentPass1!"
NEW = "StrongPass1!"


@pytest.mark.parametrize("flow", ["login", "register", "reset", "change", "admin"])
async def test_argon2_runs_with_no_transaction_open(
    flow: str,
    monkeypatch: pytest.MonkeyPatch,
    fake_session: FakeAsyncSession,
    fake_redis: InMemoryRedis,
    cache: InMemoryCache,
) -> None:
    """Argon2 waits for a bounded executor; a connection held through that wait
    lets a burst of sign-ins drain the pool."""
    user = build_user(password=OLD)
    repo = AsyncMock()
    repo.get_single.return_value = user
    repo.update.return_value = user
    repo.create.return_value = user
    uow = FakeUnitOfWork(session=fake_session, repositories={"users": repo})
    states: list[bool] = []

    async def verify(*_: object) -> bool:
        states.append(fake_session.in_transaction())
        return True

    async def hash_(_: str) -> str:
        states.append(fake_session.in_transaction())
        return user.password_hash

    modules = (
        login,
        register,
        reset_password_confirm,
        update_password,
        create_admin,
    )
    for module in modules:
        monkeypatch.setattr(module, "hash_password", hash_)
        monkeypatch.setattr(module, "verify_password", verify, raising=False)
    monkeypatch.setattr(login, "needs_password_rehash", lambda _: True)
    monkeypatch.setattr(
        login,
        "issue_session_pair",
        AsyncMock(return_value=TokenModel(access_token="a")),
    )
    monkeypatch.setattr(
        reset_password_confirm,
        "decode_one_time_token",
        AsyncMock(return_value=user.email),
    )

    if flow == "login":
        await login.LoginUserUseCase(uow, fake_redis, cache).execute(
            LoginUserModel(email=user.email, password=OLD)
        )
    elif flow == "register":
        await register.RegisterUseCase(uow, AsyncMock()).execute(
            CreateUserModel(
                first_name="John",
                last_name="Doe",
                email="john@example.com",
                username="john_doe",
                phone_number="+12025550179",
                password=NEW,
            )
        )
    elif flow == "reset":
        await reset_password_confirm.ResetPasswordConfirmUseCase(
            uow, fake_redis, cache
        ).execute(ResetPasswordModel(token="t", password=NEW))
    elif flow == "admin":
        repo.get_single.return_value = None
        await create_admin.ensure_admin(
            uow,
            email=user.email,
            password=NEW,
            first_name="Admin",
            last_name="User",
            username="admin",
            phone_number="+12025550179",
        )
    else:
        await update_password.UpdateUserPasswordUseCase(uow, fake_redis, cache).execute(
            UserNewPassword(current_password=OLD, password=NEW),
            user.id,
            Actor.user(user.id),
        )

    assert states
    assert not any(states)


async def test_password_still_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    verified = security.password_hasher.hash(OLD)
    rehashed = security.password_hasher.hash(OLD)
    changed = security.password_hasher.hash(NEW)

    assert await security.password_still_matches(OLD, verified, rehashed)
    assert not await security.password_still_matches(OLD, verified, changed)

    verify = AsyncMock()
    monkeypatch.setattr(security, "verify_password", verify)
    assert await security.password_still_matches(OLD, verified, verified)
    verify.assert_not_awaited()
