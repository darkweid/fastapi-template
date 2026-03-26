from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import jwt
import pytest

from src.core.errors.exceptions import UnauthorizedException
from src.main.config import config
from src.user.auth import dependencies
from src.user.auth.dependencies import (
    AuthenticatedUser,
    get_access_by_refresh_token,
    get_current_user,
    get_current_user_with_session,
    get_user_id_from_token,
    verify_jti,
)
from src.user.auth.redis_keys import auth_redis_keys
from src.user.models import User
from tests.factories.token_factory import build_access_payload, build_refresh_payload
from tests.factories.user_factory import build_user
from tests.fakes.db import FakeAsyncSession
from tests.fakes.redis import InMemoryRedis
from tests.helpers.requests import build_request


def encode_token(payload: dict[str, object], secret: str) -> str:
    return jwt.encode(payload, secret, config.jwt.ALGORITHM)


class FakeUserRepository:
    def __init__(self, user: User | None) -> None:
        self.get_single = AsyncMock(return_value=user)


@pytest.mark.asyncio
async def test_verify_jti_accepts_bearer_prefix(fake_redis: InMemoryRedis) -> None:
    payload = build_access_payload("user-1")
    token = encode_token(payload, config.jwt.JWT_USER_SECRET_KEY)
    await fake_redis.set(
        auth_redis_keys.access(payload["sub"], payload["session_id"]),
        payload["jti"],
        ex=60,
    )

    result = await verify_jti(f"Bearer {token}", fake_redis)

    assert result["sub"] == "user-1"


@pytest.mark.asyncio
async def test_verify_jti_expired_token(fake_redis: InMemoryRedis) -> None:
    payload = build_access_payload("user-1")
    payload["exp"] = int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp())
    token = encode_token(payload, config.jwt.JWT_USER_SECRET_KEY)

    with pytest.raises(UnauthorizedException, match="Token expired"):
        await verify_jti(token, fake_redis)


@pytest.mark.asyncio
async def test_verify_jti_invalid_signature(fake_redis: InMemoryRedis) -> None:
    payload = build_access_payload("user-1")
    token = encode_token(payload, "wrong_secret_key_for_tests_more_than_32")

    with pytest.raises(UnauthorizedException, match="Invalid token"):
        await verify_jti(token, fake_redis)


@pytest.mark.asyncio
async def test_verify_jti_invalid_structure(fake_redis: InMemoryRedis) -> None:
    payload = build_access_payload("user-1")
    payload.pop("jti")
    token = encode_token(payload, config.jwt.JWT_USER_SECRET_KEY)

    with pytest.raises(UnauthorizedException, match="Invalid token structure"):
        await verify_jti(token, fake_redis)


@pytest.mark.asyncio
async def test_verify_jti_refresh_reuse_invalidates(
    fake_redis: InMemoryRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = build_refresh_payload("user-1")
    token = encode_token(payload, config.jwt.JWT_USER_SECRET_KEY)
    await fake_redis.set(
        auth_redis_keys.refresh(payload["sub"], payload["session_id"]),
        payload["jti"],
        ex=60,
    )
    await fake_redis.setex(
        auth_redis_keys.used(payload["sub"], payload["jti"]),
        60,
        "used",
    )
    invalidate_mock = AsyncMock()
    monkeypatch.setattr(
        "src.user.auth.dependencies.invalidate_all_user_sessions",
        invalidate_mock,
    )

    with pytest.raises(UnauthorizedException, match="Token reuse detected"):
        await verify_jti(token, fake_redis)

    invalidate_mock.assert_awaited_once_with(payload["sub"], fake_redis)


@pytest.mark.asyncio
async def test_verify_jti_active_token_mismatch(fake_redis: InMemoryRedis) -> None:
    payload = build_access_payload("user-1")
    token = encode_token(payload, config.jwt.JWT_USER_SECRET_KEY)
    await fake_redis.set(
        auth_redis_keys.access(payload["sub"], payload["session_id"]),
        "other-jti",
        ex=60,
    )

    with pytest.raises(UnauthorizedException, match="Token invalidated"):
        await verify_jti(token, fake_redis)


@pytest.mark.asyncio
async def test_get_current_user_success(
    fake_redis: InMemoryRedis,
    fake_session: FakeAsyncSession,
) -> None:
    user = build_user()
    payload = build_access_payload(str(user.id))
    token = encode_token(payload, config.jwt.JWT_USER_SECRET_KEY)
    await fake_redis.set(
        auth_redis_keys.access(payload["sub"], payload["session_id"]),
        payload["jti"],
        ex=60,
    )
    user_repository = FakeUserRepository(user)

    result = await get_current_user(
        token=token,
        session=fake_session,
        redis_client=fake_redis,
        user_repository=user_repository,
    )

    assert isinstance(result, User)
    assert result.id == user.id
    user_repository.get_single.assert_awaited_once_with(fake_session, id=str(user.id))


@pytest.mark.asyncio
async def test_get_current_user_with_session_success(
    fake_redis: InMemoryRedis,
    fake_session: FakeAsyncSession,
) -> None:
    user = build_user()
    payload = build_access_payload(str(user.id))
    token = encode_token(payload, config.jwt.JWT_USER_SECRET_KEY)
    await fake_redis.set(
        auth_redis_keys.access(payload["sub"], payload["session_id"]),
        payload["jti"],
        ex=60,
    )
    user_repository = FakeUserRepository(user)

    result = await get_current_user_with_session(
        token=token,
        session=fake_session,
        redis_client=fake_redis,
        user_repository=user_repository,
    )

    assert isinstance(result, AuthenticatedUser)
    assert result.user.id == user.id
    assert result.session_id == payload["session_id"]
    user_repository.get_single.assert_awaited_once_with(fake_session, id=str(user.id))


@pytest.mark.asyncio
async def test_get_current_user_wrong_mode(
    fake_redis: InMemoryRedis, fake_session: FakeAsyncSession
) -> None:
    payload = build_refresh_payload("user-1")
    token = encode_token(payload, config.jwt.JWT_USER_SECRET_KEY)
    await fake_redis.set(
        auth_redis_keys.refresh(payload["sub"], payload["session_id"]),
        payload["jti"],
        ex=60,
    )

    with pytest.raises(UnauthorizedException):
        await get_current_user(
            token=token,
            session=fake_session,
            redis_client=fake_redis,
            user_repository=FakeUserRepository(None),
        )


@pytest.mark.asyncio
async def test_get_access_by_refresh_token_success(
    fake_redis: InMemoryRedis,
    fake_session: FakeAsyncSession,
) -> None:
    user = build_user()
    payload = build_refresh_payload(str(user.id))
    token = encode_token(payload, config.jwt.JWT_USER_SECRET_KEY)
    await fake_redis.set(
        auth_redis_keys.refresh(payload["sub"], payload["session_id"]),
        payload["jti"],
        ex=60,
    )
    user_repository = FakeUserRepository(user)

    result_user, result_payload = await get_access_by_refresh_token(
        refresh_token=token,
        session=fake_session,
        redis_client=fake_redis,
        user_repository=user_repository,
    )

    assert result_user.id == user.id
    assert result_payload["mode"] == "refresh_token"
    user_repository.get_single.assert_awaited_once_with(
        fake_session,
        id=str(user.id),
    )


@pytest.mark.asyncio
async def test_get_user_id_from_token_missing_header(
    fake_redis: InMemoryRedis,
) -> None:
    request = build_request()

    with pytest.raises(UnauthorizedException, match="Authentication token not found"):
        await get_user_id_from_token(request)


@pytest.mark.asyncio
async def test_get_user_id_from_token_success(
    fake_redis: InMemoryRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = build_access_payload("user-1")
    token = encode_token(payload, config.jwt.JWT_USER_SECRET_KEY)
    await fake_redis.set(
        auth_redis_keys.access(payload["sub"], payload["session_id"]),
        payload["jti"],
        ex=60,
    )
    request = build_request(headers={"Authorization": token})
    get_redis_mock = AsyncMock(return_value=fake_redis)
    monkeypatch.setattr(dependencies, "get_redis_client", get_redis_mock)

    result = await get_user_id_from_token(request)

    assert result == "user-1"
