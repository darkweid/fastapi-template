from unittest.mock import AsyncMock

import jwt
import pytest

from src.core.errors.exceptions import UnauthorizedException
from src.main.config import config
from src.user.auth.redis_keys import auth_redis_keys
from src.user.auth.security import rotate_refresh_token
import src.user.auth.token_helpers as token_helpers
from tests.fakes.redis import InMemoryRedis

TEST_JWT_USER_SECRET_KEY = "test-jwt-user-secret-key-not-real"

# Pinned wall clock for grace-window math; the fake's key expiry runs on
# time.monotonic(), so freezing this cannot make keys expire mid-test.
FROZEN_NOW = 1_755_000_000


def _base_payload() -> dict[str, str | int]:
    return {
        "sub": "user-id",
        "session_id": "old-session",
        "jti": "old-jti",
        "exp": 9999999999,
        "mode": "refresh_token",
    }


@pytest.fixture(autouse=True)
def _patch_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config.jwt, "REFRESH_TOKEN_EXPIRE_MINUTES", 10)
    monkeypatch.setattr(config.jwt, "JWT_USER_SECRET_KEY", TEST_JWT_USER_SECRET_KEY)
    monkeypatch.setattr(config.jwt, "ALGORITHM", "HS256")


@pytest.mark.asyncio
async def test_rotate_refresh_token_success(fake_redis: InMemoryRedis) -> None:
    """
    Given: old refresh key exists for an active refresh payload.
    When: refresh token rotation is executed.
    Then: a new token is issued, the session refresh key points to the new jti,
    and the used marker is created.
    """
    payload = _base_payload()
    await fake_redis.set(
        auth_redis_keys.refresh(str(payload["sub"]), str(payload["session_id"])),
        str(payload["jti"]),
        ex=config.jwt.REFRESH_TOKEN_EXPIRE_MINUTES * 60,
    )

    token = await rotate_refresh_token(payload, fake_redis)
    decoded = jwt.decode(
        token, config.jwt.JWT_USER_SECRET_KEY, algorithms=[config.jwt.ALGORITHM]
    )

    assert decoded["session_id"] == payload["session_id"]
    assert decoded["jti"] != payload["jti"]

    used_key = auth_redis_keys.used(str(payload["sub"]), str(payload["jti"]))
    old_refresh_key = auth_redis_keys.refresh(
        str(payload["sub"]),
        str(payload["session_id"]),
    )
    assert await fake_redis.exists(used_key) == 1
    assert await fake_redis.exists(old_refresh_key) == 1
    assert await fake_redis.get(old_refresh_key) == decoded["jti"]


@pytest.mark.asyncio
async def test_used_marker_ttl_tracks_the_refresh_token_lifetime(
    fake_redis: InMemoryRedis,
) -> None:
    # The marker must cover the rotated-out token's whole remaining lifetime:
    # shorter, and a replayed copy after marker expiry reads as INVALID instead
    # of REUSED; longer buys nothing because the token itself has expired.
    payload = _base_payload()
    await fake_redis.set(
        auth_redis_keys.refresh(str(payload["sub"]), str(payload["session_id"])),
        str(payload["jti"]),
        ex=config.jwt.REFRESH_TOKEN_EXPIRE_MINUTES * 60,
    )

    await rotate_refresh_token(payload, fake_redis)

    used_key = auth_redis_keys.used(str(payload["sub"]), str(payload["jti"]))
    assert (
        await fake_redis.ttl(used_key) == config.jwt.REFRESH_TOKEN_EXPIRE_MINUTES * 60
    )


@pytest.mark.asyncio
async def test_rotation_stores_a_unix_timestamp_in_the_used_marker(
    fake_redis: InMemoryRedis,
) -> None:
    # The marker value is the rotation instant: the grace check compares it
    # against the Redis server clock, so anything else breaks the window math.
    payload = _base_payload()
    fake_redis.wall_clock = lambda: float(FROZEN_NOW)
    await fake_redis.set(
        auth_redis_keys.refresh(str(payload["sub"]), str(payload["session_id"])),
        str(payload["jti"]),
        ex=config.jwt.REFRESH_TOKEN_EXPIRE_MINUTES * 60,
    )

    await rotate_refresh_token(payload, fake_redis)

    used_key = auth_redis_keys.used(str(payload["sub"]), str(payload["jti"]))
    assert await fake_redis.get(used_key) == str(FROZEN_NOW)


@pytest.mark.asyncio
async def test_second_rotation_within_grace_rejects_without_family_wipe(
    fake_redis: InMemoryRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given: the same refresh token is submitted twice within the grace window
    (a network retry / double-submit, not an attack).
    When: the second rotation runs.
    Then: it is rejected with the generic invalid-token answer, and the
    session family is NOT wiped.
    """
    monkeypatch.setattr(config.jwt, "REFRESH_TOKEN_REUSE_GRACE_SECONDS", 10)
    payload = _base_payload()
    fake_redis.wall_clock = lambda: float(FROZEN_NOW)
    await fake_redis.set(
        auth_redis_keys.refresh(str(payload["sub"]), str(payload["session_id"])),
        str(payload["jti"]),
        ex=config.jwt.REFRESH_TOKEN_EXPIRE_MINUTES * 60,
    )
    await rotate_refresh_token(payload, fake_redis)

    invalidate_mock = AsyncMock()
    monkeypatch.setattr(token_helpers, "invalidate_all_user_sessions", invalidate_mock)

    with pytest.raises(UnauthorizedException, match="Token invalidated or expired"):
        await rotate_refresh_token(payload, fake_redis)

    invalidate_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_replay_after_the_grace_window_wipes_the_family(
    fake_redis: InMemoryRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config.jwt, "REFRESH_TOKEN_REUSE_GRACE_SECONDS", 10)
    payload = _base_payload()
    fake_redis.wall_clock = lambda: float(FROZEN_NOW)
    await fake_redis.set(
        auth_redis_keys.refresh(str(payload["sub"]), str(payload["session_id"])),
        str(payload["jti"]),
        ex=config.jwt.REFRESH_TOKEN_EXPIRE_MINUTES * 60,
    )
    await rotate_refresh_token(payload, fake_redis)

    fake_redis.wall_clock = lambda: float(FROZEN_NOW + 11)
    invalidate_mock = AsyncMock()
    monkeypatch.setattr(token_helpers, "invalidate_all_user_sessions", invalidate_mock)

    with pytest.raises(UnauthorizedException, match="Token reuse detected"):
        await rotate_refresh_token(payload, fake_redis)

    invalidate_mock.assert_awaited_once_with(payload["sub"], fake_redis)


@pytest.mark.asyncio
async def test_zero_grace_disables_the_window(
    fake_redis: InMemoryRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config.jwt, "REFRESH_TOKEN_REUSE_GRACE_SECONDS", 0)
    payload = _base_payload()
    fake_redis.wall_clock = lambda: float(FROZEN_NOW)
    await fake_redis.set(
        auth_redis_keys.refresh(str(payload["sub"]), str(payload["session_id"])),
        str(payload["jti"]),
        ex=config.jwt.REFRESH_TOKEN_EXPIRE_MINUTES * 60,
    )
    await rotate_refresh_token(payload, fake_redis)

    invalidate_mock = AsyncMock()
    monkeypatch.setattr(token_helpers, "invalidate_all_user_sessions", invalidate_mock)

    with pytest.raises(UnauthorizedException, match="Token reuse detected"):
        await rotate_refresh_token(payload, fake_redis)

    invalidate_mock.assert_awaited_once_with(payload["sub"], fake_redis)


@pytest.mark.asyncio
async def test_rotate_refresh_token_reuse_detected(
    fake_redis: InMemoryRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given: used marker for the incoming refresh jti already exists, with a
    non-numeric value (legacy or corrupted marker - no timestamp to compare).
    When: refresh token rotation is executed for that payload.
    Then: the grace window cannot apply, UnauthorizedException is raised and
    all sessions for the user are invalidated.
    """
    payload = _base_payload()
    await fake_redis.setex(
        auth_redis_keys.used(str(payload["sub"]), str(payload["jti"])),
        100,
        "used",
    )
    invalidate_mock = AsyncMock()
    monkeypatch.setattr(token_helpers, "invalidate_all_user_sessions", invalidate_mock)

    with pytest.raises(UnauthorizedException):
        await rotate_refresh_token(payload, fake_redis)

    invalidate_mock.assert_awaited_once_with(payload["sub"], fake_redis)


@pytest.mark.asyncio
async def test_rotate_refresh_token_invalid_state(
    fake_redis: InMemoryRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given: stored refresh jti does not match jti from payload.
    When: refresh token rotation is executed.
    Then: UnauthorizedException is raised and all sessions for the user are invalidated.
    """
    payload = _base_payload()
    await fake_redis.set(
        auth_redis_keys.refresh(str(payload["sub"]), str(payload["session_id"])),
        "wrong-jti",
        ex=config.jwt.REFRESH_TOKEN_EXPIRE_MINUTES * 60,
    )
    invalidate_mock = AsyncMock()
    monkeypatch.setattr(token_helpers, "invalidate_all_user_sessions", invalidate_mock)

    with pytest.raises(UnauthorizedException):
        await rotate_refresh_token(payload, fake_redis)

    invalidate_mock.assert_awaited_once_with(payload["sub"], fake_redis)


@pytest.mark.asyncio
async def test_rotate_refresh_token_missing_jti_invalidates(
    fake_redis: InMemoryRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given: refresh payload misses jti field.
    When: refresh token rotation is executed.
    Then: UnauthorizedException is raised and all sessions for the user are invalidated.
    """
    invalidate_mock = AsyncMock()
    monkeypatch.setattr(token_helpers, "invalidate_all_user_sessions", invalidate_mock)

    payload = _base_payload()
    payload.pop("jti")

    with pytest.raises(UnauthorizedException):
        await rotate_refresh_token(payload, fake_redis)

    invalidate_mock.assert_awaited_once_with(payload["sub"], fake_redis)
