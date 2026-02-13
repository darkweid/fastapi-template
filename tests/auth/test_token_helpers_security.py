from datetime import datetime, timezone
from unittest.mock import AsyncMock

import jwt
import pytest

from src.core.errors.exceptions import UnauthorizedException
from src.user.auth.jwt_payload_schema import JWTPayload
import src.user.auth.security as security
import src.user.auth.token_helpers as token_helpers
from tests.fakes.redis import InMemoryRedis
from tests.helpers.providers import ProvideValue


def access_jti_key(user_id: str, session_id: str) -> str:
    return f"access:{user_id}:{session_id}"


def refresh_jti_key(user_id: str, session_id: str) -> str:
    return f"refresh:{user_id}:{session_id}"


def refresh_family_key(user_id: str, family_id: str) -> str:
    return f"family:{user_id}:{family_id}"


def used_refresh_key(user_id: str, jti: str) -> str:
    return f"used:{user_id}:{jti}"


@pytest.fixture(autouse=True)
def _patch_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(security.config.jwt, "JWT_USER_SECRET_KEY", "secret")
    monkeypatch.setattr(security.config.jwt, "JWT_VERIFY_SECRET_KEY", "verify")
    monkeypatch.setattr(security.config.jwt, "JWT_RESET_PASSWORD_SECRET_KEY", "reset")
    monkeypatch.setattr(security.config.jwt, "ALGORITHM", "HS256")
    monkeypatch.setattr(security.config.jwt, "ACCESS_TOKEN_EXPIRE_MINUTES", 5)
    monkeypatch.setattr(security.config.jwt, "REFRESH_TOKEN_EXPIRE_MINUTES", 10)
    monkeypatch.setattr(security.config.jwt, "REFRESH_TOKEN_USED_TTL_SECONDS", 100)
    monkeypatch.setattr(security.config.jwt, "VERIFICATION_TOKEN_EXPIRE_MINUTES", 5)
    monkeypatch.setattr(security.config.jwt, "RESET_PASSWORD_TOKEN_EXPIRE_MINUTES", 5)


@pytest.mark.asyncio
async def test_create_access_token_stores_jti(
    fake_redis: InMemoryRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given: fixed time and an empty Redis state for user sessions.
    When: an access token is created for subject "user".
    Then: token payload contains access mode and Redis stores jti by (sub, session_id).
    """
    fixed_now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(security, "get_utc_now", ProvideValue(fixed_now))

    token = await security.create_access_token({"sub": "user"}, redis_client=fake_redis)
    decoded = jwt.decode(
        token, "secret", algorithms=["HS256"], options={"verify_exp": False}
    )

    assert decoded["sub"] == "user"
    assert decoded["mode"] == "access_token"
    stored_jti = await fake_redis.get(
        access_jti_key(decoded["sub"], decoded["session_id"])
    )
    assert stored_jti == decoded["jti"]


@pytest.mark.asyncio
async def test_create_refresh_token_stores_family(
    fake_redis: InMemoryRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given: fixed time and an empty Redis state for refresh data.
    When: a refresh token is created for subject "user".
    Then: token contains family id, refresh jti is stored, and family key is active.
    """
    fixed_now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(security, "get_utc_now", ProvideValue(fixed_now))

    token = await security.create_refresh_token(
        {"sub": "user"}, redis_client=fake_redis
    )
    decoded = jwt.decode(
        token, "secret", algorithms=["HS256"], options={"verify_exp": False}
    )

    assert decoded["mode"] == "refresh_token"
    assert "family" in decoded
    assert (
        await fake_redis.get(refresh_jti_key(decoded["sub"], decoded["session_id"]))
        == decoded["jti"]
    )
    assert (
        await fake_redis.exists(refresh_family_key(decoded["sub"], decoded["family"]))
        == 1
    )


@pytest.mark.asyncio
async def test_validate_token_structure_missing_fields(
    fake_redis: InMemoryRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given: a refresh payload is missing required jti/family fields.
    When: refresh token structure validation is executed.
    Then: UnauthorizedException is raised and all sessions for that user are invalidated.
    """
    invalidate_mock = AsyncMock()
    monkeypatch.setattr(token_helpers, "invalidate_all_user_sessions", invalidate_mock)
    payload: JWTPayload = {"sub": "u1", "session_id": "s1", "mode": "refresh_token"}

    with pytest.raises(UnauthorizedException):
        await token_helpers.validate_token_structure(payload, fake_redis)

    invalidate_mock.assert_awaited_once_with("u1", fake_redis)


@pytest.mark.asyncio
async def test_validate_token_family_missing_family(
    fake_redis: InMemoryRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given: refresh family id is missing.
    When: token family validation is executed.
    Then: UnauthorizedException is raised and all sessions for that user are invalidated.
    """
    invalidate_mock = AsyncMock()
    monkeypatch.setattr(token_helpers, "invalidate_all_user_sessions", invalidate_mock)

    with pytest.raises(UnauthorizedException):
        await token_helpers.validate_token_family("u1", None, fake_redis)

    invalidate_mock.assert_awaited_once_with("u1", fake_redis)


@pytest.mark.asyncio
async def test_validate_token_family_not_exists(
    fake_redis: InMemoryRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given: family key is absent for the provided user in Redis.
    When: token family validation is executed.
    Then: UnauthorizedException is raised and all sessions for that user are invalidated.
    """
    invalidate_mock = AsyncMock()
    monkeypatch.setattr(token_helpers, "invalidate_all_user_sessions", invalidate_mock)

    with pytest.raises(UnauthorizedException):
        await token_helpers.validate_token_family("u1", "family", fake_redis)

    invalidate_mock.assert_awaited_once_with("u1", fake_redis)


@pytest.mark.asyncio
async def test_execute_token_rotation_reused(
    fake_redis: InMemoryRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given: used-token marker already exists for user and jti.
    When: refresh token rotation is executed for the same jti.
    Then: UnauthorizedException is raised and all sessions for that user are invalidated.
    """
    await fake_redis.setex(used_refresh_key("u1", "j1"), 100, "used")
    invalidate_mock = AsyncMock()
    monkeypatch.setattr(token_helpers, "invalidate_all_user_sessions", invalidate_mock)

    with pytest.raises(UnauthorizedException):
        await token_helpers.execute_token_rotation("u1", "s1", "j1", fake_redis)

    invalidate_mock.assert_awaited_once_with("u1", fake_redis)


@pytest.mark.asyncio
async def test_execute_token_rotation_invalid(
    fake_redis: InMemoryRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given: stored refresh jti differs from provided jti.
    When: refresh token rotation is executed.
    Then: UnauthorizedException is raised and all sessions for that user are invalidated.
    """
    await fake_redis.set(refresh_jti_key("u1", "s1"), "wrong-jti", ex=600)
    invalidate_mock = AsyncMock()
    monkeypatch.setattr(token_helpers, "invalidate_all_user_sessions", invalidate_mock)

    with pytest.raises(UnauthorizedException):
        await token_helpers.execute_token_rotation("u1", "s1", "j1", fake_redis)

    invalidate_mock.assert_awaited_once_with("u1", fake_redis)


@pytest.mark.asyncio
async def test_execute_token_rotation_ok(fake_redis: InMemoryRedis) -> None:
    """
    Given: active refresh key exists and no used-token marker is present.
    When: refresh token rotation is executed with matching jti.
    Then: rotation returns OK, old refresh key is removed, and used marker is created.
    """
    await fake_redis.set(refresh_jti_key("u1", "s1"), "j1", ex=600)
    result = await token_helpers.execute_token_rotation("u1", "s1", "j1", fake_redis)

    assert result == "OK"
    assert await fake_redis.exists(used_refresh_key("u1", "j1")) == 1
    assert await fake_redis.exists(refresh_jti_key("u1", "s1")) == 0


@pytest.mark.asyncio
async def test_invalidate_all_user_sessions(fake_redis: InMemoryRedis) -> None:
    """
    Given: user has active access/refresh/family/used session keys in Redis.
    When: user session invalidation is executed.
    Then: all those keys are removed for that user.
    """
    await fake_redis.set(access_jti_key("1", "a1"), "x", ex=60)
    await fake_redis.set(refresh_jti_key("1", "r1"), "x", ex=60)
    await fake_redis.set(refresh_family_key("1", "f1"), "x", ex=60)
    await fake_redis.set(used_refresh_key("1", "u1"), "x", ex=60)

    await token_helpers.invalidate_all_user_sessions("1", fake_redis)

    assert await fake_redis.exists(access_jti_key("1", "a1")) == 0
    assert await fake_redis.exists(refresh_jti_key("1", "r1")) == 0
    assert await fake_redis.exists(refresh_family_key("1", "f1")) == 0
    assert await fake_redis.exists(used_refresh_key("1", "u1")) == 0


@pytest.mark.asyncio
async def test_invalidate_all_user_sessions_keeps_other_users_keys(
    fake_redis: InMemoryRedis,
) -> None:
    """
    Given: session keys exist for target user and another user.
    When: invalidation is executed for only the target user.
    Then: target user keys are deleted and other user keys remain untouched.
    """
    await fake_redis.set(access_jti_key("1", "a1"), "x", ex=60)
    await fake_redis.set(refresh_jti_key("1", "r1"), "x", ex=60)
    await fake_redis.set(access_jti_key("2", "a2"), "x", ex=60)
    await fake_redis.set(refresh_jti_key("2", "r2"), "x", ex=60)

    await token_helpers.invalidate_all_user_sessions("1", fake_redis)

    assert await fake_redis.exists(access_jti_key("1", "a1")) == 0
    assert await fake_redis.exists(refresh_jti_key("1", "r1")) == 0
    assert await fake_redis.exists(access_jti_key("2", "a2")) == 1
    assert await fake_redis.exists(refresh_jti_key("2", "r2")) == 1
