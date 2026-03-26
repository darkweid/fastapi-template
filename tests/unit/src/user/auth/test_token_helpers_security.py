from datetime import datetime, timezone
from unittest.mock import AsyncMock

import jwt
import pytest

from src.core.errors.exceptions import UnauthorizedException
from src.user.auth.dependencies import verify_jti
from src.user.auth.jwt_payload_schema import JWTPayload
from src.user.auth.redis_keys import OneTimeTokenPurpose, auth_redis_keys
import src.user.auth.security as security
import src.user.auth.token_helpers as token_helpers
from tests.fakes.redis import InMemoryRedis
from tests.helpers.providers import ProvideValue

TEST_JWT_USER_SECRET_KEY = "test-jwt-user-secret-key-not-real"
TEST_JWT_VERIFY_SECRET_KEY = "test-jwt-verify-secret-key-not-real"
TEST_JWT_RESET_SECRET_KEY = "test-jwt-reset-secret-key-not-real"


def access_jti_key(user_id: str, session_id: str) -> str:
    return auth_redis_keys.access(user_id, session_id)


def refresh_jti_key(user_id: str, session_id: str) -> str:
    return auth_redis_keys.refresh(user_id, session_id)


def used_refresh_key(user_id: str, jti: str) -> str:
    return auth_redis_keys.used(user_id, jti)


def one_time_token_key(purpose: OneTimeTokenPurpose, email: str) -> str:
    return auth_redis_keys.one_time_token(purpose, email)


@pytest.fixture(autouse=True)
def _patch_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        security.config.jwt,
        "JWT_USER_SECRET_KEY",
        TEST_JWT_USER_SECRET_KEY,
    )
    monkeypatch.setattr(
        security.config.jwt,
        "JWT_VERIFY_SECRET_KEY",
        TEST_JWT_VERIFY_SECRET_KEY,
    )
    monkeypatch.setattr(
        security.config.jwt,
        "JWT_RESET_PASSWORD_SECRET_KEY",
        TEST_JWT_RESET_SECRET_KEY,
    )
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
        token,
        TEST_JWT_USER_SECRET_KEY,
        algorithms=["HS256"],
        options={"verify_exp": False},
    )

    assert decoded["sub"] == "user"
    assert decoded["mode"] == "access_token"
    stored_jti = await fake_redis.get(
        access_jti_key(decoded["sub"], decoded["session_id"])
    )
    assert stored_jti == decoded["jti"]


@pytest.mark.asyncio
async def test_create_access_token_ignores_removed_family_claim(
    fake_redis: InMemoryRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixed_now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(security, "get_utc_now", ProvideValue(fixed_now))

    token = await security.create_access_token(
        {"sub": "user", "family": "family-1"},
        redis_client=fake_redis,
        session_id="session-1",
    )
    decoded = jwt.decode(
        token,
        TEST_JWT_USER_SECRET_KEY,
        algorithms=["HS256"],
        options={"verify_exp": False},
    )

    assert "family" not in decoded
    assert decoded["session_id"] == "session-1"


@pytest.mark.asyncio
async def test_create_refresh_token_stores_jti(
    fake_redis: InMemoryRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given: fixed time and an empty Redis state for refresh data.
    When: a refresh token is created for subject "user".
    Then: refresh jti is stored under the active session key.
    """
    fixed_now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(security, "get_utc_now", ProvideValue(fixed_now))

    token = await security.create_refresh_token(
        {"sub": "user"}, redis_client=fake_redis
    )
    decoded = jwt.decode(
        token,
        TEST_JWT_USER_SECRET_KEY,
        algorithms=["HS256"],
        options={"verify_exp": False},
    )

    assert decoded["mode"] == "refresh_token"
    assert (
        await fake_redis.get(refresh_jti_key(decoded["sub"], decoded["session_id"]))
        == decoded["jti"]
    )


@pytest.mark.asyncio
async def test_create_verification_token_stores_active_jti(
    fake_redis: InMemoryRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixed_now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(security, "get_utc_now", ProvideValue(fixed_now))

    token = await security.create_verification_token(
        {"email": "User@Example.com"},
        redis_client=fake_redis,
    )
    decoded = jwt.decode(
        token,
        TEST_JWT_VERIFY_SECRET_KEY,
        algorithms=["HS256"],
        options={"verify_exp": False},
    )

    assert decoded["mode"] == "verification_token"
    assert decoded["email"] == "user@example.com"
    assert decoded["sub"] == "user@example.com"
    assert decoded["jti"]
    assert (
        await fake_redis.get(one_time_token_key("verification", "user@example.com"))
        == decoded["jti"]
    )


@pytest.mark.asyncio
async def test_create_verification_token_invalidates_previous_jti(
    fake_redis: InMemoryRedis,
) -> None:
    first_token = await security.create_verification_token(
        {"email": "user@example.com"},
        redis_client=fake_redis,
    )
    second_token = await security.create_verification_token(
        {"email": "user@example.com"},
        redis_client=fake_redis,
    )

    first_decoded = jwt.decode(
        first_token,
        TEST_JWT_VERIFY_SECRET_KEY,
        algorithms=["HS256"],
        options={"verify_exp": False},
    )
    second_decoded = jwt.decode(
        second_token,
        TEST_JWT_VERIFY_SECRET_KEY,
        algorithms=["HS256"],
        options={"verify_exp": False},
    )

    assert first_decoded["jti"] != second_decoded["jti"]
    assert (
        await fake_redis.get(one_time_token_key("verification", "user@example.com"))
        == second_decoded["jti"]
    )


@pytest.mark.asyncio
async def test_create_reset_password_token_stores_active_jti(
    fake_redis: InMemoryRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixed_now = datetime(2024, 1, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(security, "get_utc_now", ProvideValue(fixed_now))

    token = await security.create_reset_password_token(
        {"email": "User@Example.com"},
        redis_client=fake_redis,
    )
    decoded = jwt.decode(
        token,
        TEST_JWT_RESET_SECRET_KEY,
        algorithms=["HS256"],
        options={"verify_exp": False},
    )

    assert decoded["mode"] == "reset_password_token"
    assert decoded["email"] == "user@example.com"
    assert decoded["sub"] == "user@example.com"
    assert decoded["jti"]
    assert (
        await fake_redis.get(one_time_token_key("reset_password", "user@example.com"))
        == decoded["jti"]
    )


@pytest.mark.asyncio
async def test_create_reset_password_token_invalidates_previous_jti(
    fake_redis: InMemoryRedis,
) -> None:
    first_token = await security.create_reset_password_token(
        {"email": "user@example.com"},
        redis_client=fake_redis,
    )
    second_token = await security.create_reset_password_token(
        {"email": "user@example.com"},
        redis_client=fake_redis,
    )

    first_decoded = jwt.decode(
        first_token,
        TEST_JWT_RESET_SECRET_KEY,
        algorithms=["HS256"],
        options={"verify_exp": False},
    )
    second_decoded = jwt.decode(
        second_token,
        TEST_JWT_RESET_SECRET_KEY,
        algorithms=["HS256"],
        options={"verify_exp": False},
    )

    assert first_decoded["jti"] != second_decoded["jti"]
    assert (
        await fake_redis.get(one_time_token_key("reset_password", "user@example.com"))
        == second_decoded["jti"]
    )


@pytest.mark.asyncio
async def test_validate_token_structure_missing_fields(
    fake_redis: InMemoryRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given: a refresh payload is missing required jti field.
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
    Given: user has active access, refresh, and used session keys in Redis.
    When: user session invalidation is executed.
    Then: all those keys are removed for that user.
    """
    await fake_redis.set(access_jti_key("1", "a1"), "x", ex=60)
    await fake_redis.set(refresh_jti_key("1", "r1"), "x", ex=60)
    await fake_redis.set(used_refresh_key("1", "u1"), "x", ex=60)

    await token_helpers.invalidate_all_user_sessions("1", fake_redis)

    assert await fake_redis.exists(access_jti_key("1", "a1")) == 0
    assert await fake_redis.exists(refresh_jti_key("1", "r1")) == 0
    assert await fake_redis.exists(used_refresh_key("1", "u1")) == 0


@pytest.mark.asyncio
async def test_invalidate_user_session_removes_only_target_session(
    fake_redis: InMemoryRedis,
) -> None:
    """
    Given: Redis contains auth keys for multiple sessions of the same user.
    When: a single session is invalidated.
    Then: the target session access/refresh keys are removed.
    """
    await fake_redis.set(access_jti_key("1", "s1"), "x", ex=60)
    await fake_redis.set(refresh_jti_key("1", "s1"), "x", ex=60)
    await fake_redis.set(access_jti_key("1", "s2"), "y", ex=60)
    await fake_redis.set(refresh_jti_key("1", "s2"), "y", ex=60)
    await token_helpers.invalidate_user_session("1", "s1", fake_redis)

    assert await fake_redis.exists(access_jti_key("1", "s1")) == 0
    assert await fake_redis.exists(refresh_jti_key("1", "s1")) == 0
    assert await fake_redis.exists(access_jti_key("1", "s2")) == 1
    assert await fake_redis.exists(refresh_jti_key("1", "s2")) == 1


@pytest.mark.asyncio
async def test_refresh_rotation_invalidates_previous_access_token_for_same_session(
    fake_redis: InMemoryRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed_now = datetime.now(timezone.utc)
    monkeypatch.setattr(security, "get_utc_now", ProvideValue(fixed_now))

    access_token_before_refresh = await security.create_access_token(
        {"sub": "user", "family": "family-1"},
        redis_client=fake_redis,
        session_id="session-1",
    )
    refresh_token = await security.create_refresh_token(
        {"sub": "user"},
        redis_client=fake_redis,
        session_id="session-1",
    )
    refresh_payload = jwt.decode(
        refresh_token,
        TEST_JWT_USER_SECRET_KEY,
        algorithms=["HS256"],
        options={"verify_exp": False},
    )

    rotated_refresh_token = await security.rotate_refresh_token(
        refresh_payload,
        fake_redis,
    )
    rotated_refresh_payload = jwt.decode(
        rotated_refresh_token,
        TEST_JWT_USER_SECRET_KEY,
        algorithms=["HS256"],
        options={"verify_exp": False},
    )
    access_token_after_refresh = await security.create_access_token(
        {"sub": "user"},
        redis_client=fake_redis,
        session_id=rotated_refresh_payload["session_id"],
    )

    with pytest.raises(UnauthorizedException, match="Token invalidated"):
        await verify_jti(access_token_before_refresh, fake_redis)

    verified_payload = await verify_jti(access_token_after_refresh, fake_redis)

    assert verified_payload["session_id"] == "session-1"
    assert "family" not in verified_payload


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
