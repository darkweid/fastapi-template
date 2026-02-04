from unittest.mock import AsyncMock

import jwt
import pytest

from src.core.errors.exceptions import UnauthorizedException
from src.main.config import config
from src.user.auth.security import rotate_refresh_token
import src.user.auth.token_helpers as token_helpers
from tests.fakes.redis import InMemoryRedis


def _base_payload() -> dict[str, str | int]:
    return {
        "sub": "user-id",
        "session_id": "old-session",
        "jti": "old-jti",
        "family": "family-id",
        "exp": 9999999999,
        "mode": "refresh_token",
    }


@pytest.fixture(autouse=True)
def _patch_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.jwt, "REFRESH_TOKEN_USED_TTL_SECONDS", 100)
    monkeypatch.setattr(config.jwt, "REFRESH_TOKEN_EXPIRE_MINUTES", 10)
    monkeypatch.setattr(config.jwt, "JWT_USER_SECRET_KEY", "secret")
    monkeypatch.setattr(config.jwt, "ALGORITHM", "HS256")


@pytest.mark.asyncio
async def test_rotate_refresh_token_success(fake_redis: InMemoryRedis) -> None:
    payload = _base_payload()
    await fake_redis.set(
        f"family:{payload['sub']}:{payload['family']}",
        "active",
        ex=config.jwt.REFRESH_TOKEN_EXPIRE_MINUTES * 60,
    )
    await fake_redis.set(
        f"refresh:{payload['sub']}:{payload['session_id']}",
        str(payload["jti"]),
        ex=config.jwt.REFRESH_TOKEN_EXPIRE_MINUTES * 60,
    )

    token = await rotate_refresh_token(payload, fake_redis)
    decoded = jwt.decode(
        token, config.jwt.JWT_USER_SECRET_KEY, algorithms=[config.jwt.ALGORITHM]
    )

    assert decoded["family"] == payload["family"]
    assert decoded["session_id"] != payload["session_id"]
    assert decoded["jti"] != payload["jti"]

    used_key = f"used:{payload['sub']}:{payload['jti']}"
    old_refresh_key = f"refresh:{payload['sub']}:{payload['session_id']}"
    family_key = f"family:{payload['sub']}:{payload['family']}"
    assert await fake_redis.exists(used_key) == 1
    assert await fake_redis.exists(old_refresh_key) == 0
    assert await fake_redis.exists(family_key) == 1
    assert await fake_redis.ttl(family_key) > 0


@pytest.mark.asyncio
async def test_rotate_refresh_token_reuse_detected(
    fake_redis: InMemoryRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _base_payload()
    await fake_redis.set(
        f"family:{payload['sub']}:{payload['family']}",
        "active",
        ex=config.jwt.REFRESH_TOKEN_EXPIRE_MINUTES * 60,
    )
    await fake_redis.setex(f"used:{payload['sub']}:{payload['jti']}", 100, "used")
    invalidate_mock = AsyncMock()
    monkeypatch.setattr(token_helpers, "invalidate_all_user_sessions", invalidate_mock)

    with pytest.raises(UnauthorizedException):
        await rotate_refresh_token(payload, fake_redis)

    invalidate_mock.assert_awaited_once_with(payload["sub"], fake_redis)


@pytest.mark.asyncio
async def test_rotate_refresh_token_invalid_state(
    fake_redis: InMemoryRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = _base_payload()
    await fake_redis.set(
        f"family:{payload['sub']}:{payload['family']}",
        "active",
        ex=config.jwt.REFRESH_TOKEN_EXPIRE_MINUTES * 60,
    )
    await fake_redis.set(
        f"refresh:{payload['sub']}:{payload['session_id']}",
        "wrong-jti",
        ex=config.jwt.REFRESH_TOKEN_EXPIRE_MINUTES * 60,
    )
    invalidate_mock = AsyncMock()
    monkeypatch.setattr(token_helpers, "invalidate_all_user_sessions", invalidate_mock)

    with pytest.raises(UnauthorizedException):
        await rotate_refresh_token(payload, fake_redis)

    invalidate_mock.assert_awaited_once_with(payload["sub"], fake_redis)


@pytest.mark.asyncio
async def test_rotate_refresh_token_missing_family_invalidates(
    fake_redis: InMemoryRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    invalidate_mock = AsyncMock()
    monkeypatch.setattr(token_helpers, "invalidate_all_user_sessions", invalidate_mock)

    payload = _base_payload()
    payload.pop("family")

    with pytest.raises(UnauthorizedException):
        await rotate_refresh_token(payload, fake_redis)

    invalidate_mock.assert_awaited_once_with(payload["sub"], fake_redis)


@pytest.mark.asyncio
async def test_rotate_refresh_token_missing_jti_invalidates(
    fake_redis: InMemoryRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    invalidate_mock = AsyncMock()
    monkeypatch.setattr(token_helpers, "invalidate_all_user_sessions", invalidate_mock)

    payload = _base_payload()
    payload.pop("jti")

    with pytest.raises(UnauthorizedException):
        await rotate_refresh_token(payload, fake_redis)

    invalidate_mock.assert_awaited_once_with(payload["sub"], fake_redis)


@pytest.mark.asyncio
async def test_rotate_refresh_token_family_not_found(
    fake_redis: InMemoryRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    invalidate_mock = AsyncMock()
    monkeypatch.setattr(token_helpers, "invalidate_all_user_sessions", invalidate_mock)

    payload = _base_payload()

    with pytest.raises(UnauthorizedException):
        await rotate_refresh_token(payload, fake_redis)

    invalidate_mock.assert_awaited_once_with(payload["sub"], fake_redis)
