import asyncio
from unittest.mock import AsyncMock

import jwt
import pytest

from src.core.auth.credentials import verify_jti
from src.core.auth.jwt_payload_schema import JWTPayload
from src.core.auth.session_issuance import issue_session_pair
import src.core.auth.token_helpers as token_helpers
from src.core.auth.tokens import rotate_session_tokens
from src.core.errors.exceptions import UnauthorizedException
from src.core.schemas import TokenModel
from src.main.config import config
from src.user.auth.realm import USER_AUTH_REALM
from tests.fakes.redis import InMemoryRedis

TEST_JWT_USER_SECRET_KEY = "test-jwt-user-secret-key-not-real"

AUTH_KEYS = USER_AUTH_REALM.keys

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
async def test_rotate_session_tokens_success(fake_redis: InMemoryRedis) -> None:
    """
    Given: old refresh key exists for an active refresh payload.
    When: refresh token rotation is executed.
    Then: a new token is issued, the session refresh key points to the new jti,
    and the used marker is created.
    """
    payload = _base_payload()
    await fake_redis.set(
        AUTH_KEYS.refresh(str(payload["sub"]), str(payload["session_id"])),
        str(payload["jti"]),
        ex=config.jwt.REFRESH_TOKEN_EXPIRE_MINUTES * 60,
    )

    tokens = await rotate_session_tokens(payload, fake_redis, realm=USER_AUTH_REALM)
    decoded = jwt.decode(
        tokens.refresh_token,
        config.jwt.JWT_USER_SECRET_KEY,
        algorithms=[config.jwt.ALGORITHM],
    )

    assert decoded["session_id"] == payload["session_id"]
    assert decoded["jti"] != payload["jti"]

    used_key = AUTH_KEYS.used(str(payload["sub"]), str(payload["jti"]))
    old_refresh_key = AUTH_KEYS.refresh(
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
        AUTH_KEYS.refresh(str(payload["sub"]), str(payload["session_id"])),
        str(payload["jti"]),
        ex=config.jwt.REFRESH_TOKEN_EXPIRE_MINUTES * 60,
    )

    await rotate_session_tokens(payload, fake_redis, realm=USER_AUTH_REALM)

    used_key = AUTH_KEYS.used(str(payload["sub"]), str(payload["jti"]))
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
        AUTH_KEYS.refresh(str(payload["sub"]), str(payload["session_id"])),
        str(payload["jti"]),
        ex=config.jwt.REFRESH_TOKEN_EXPIRE_MINUTES * 60,
    )

    await rotate_session_tokens(payload, fake_redis, realm=USER_AUTH_REALM)

    used_key = AUTH_KEYS.used(str(payload["sub"]), str(payload["jti"]))
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
        AUTH_KEYS.refresh(str(payload["sub"]), str(payload["session_id"])),
        str(payload["jti"]),
        ex=config.jwt.REFRESH_TOKEN_EXPIRE_MINUTES * 60,
    )
    await rotate_session_tokens(payload, fake_redis, realm=USER_AUTH_REALM)

    invalidate_mock = AsyncMock()
    monkeypatch.setattr(token_helpers, "invalidate_all_sessions", invalidate_mock)

    with pytest.raises(UnauthorizedException, match="Token invalidated or expired"):
        await rotate_session_tokens(payload, fake_redis, realm=USER_AUTH_REALM)

    invalidate_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_replay_after_the_grace_window_wipes_the_family(
    fake_redis: InMemoryRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config.jwt, "REFRESH_TOKEN_REUSE_GRACE_SECONDS", 10)
    payload = _base_payload()
    fake_redis.wall_clock = lambda: float(FROZEN_NOW)
    await fake_redis.set(
        AUTH_KEYS.refresh(str(payload["sub"]), str(payload["session_id"])),
        str(payload["jti"]),
        ex=config.jwt.REFRESH_TOKEN_EXPIRE_MINUTES * 60,
    )
    await rotate_session_tokens(payload, fake_redis, realm=USER_AUTH_REALM)

    fake_redis.wall_clock = lambda: float(FROZEN_NOW + 11)
    invalidate_mock = AsyncMock()
    monkeypatch.setattr(token_helpers, "invalidate_all_sessions", invalidate_mock)

    with pytest.raises(UnauthorizedException, match="Token reuse detected"):
        await rotate_session_tokens(payload, fake_redis, realm=USER_AUTH_REALM)

    invalidate_mock.assert_awaited_once_with(payload["sub"], fake_redis, keys=AUTH_KEYS)


@pytest.mark.asyncio
async def test_zero_grace_disables_the_window(
    fake_redis: InMemoryRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config.jwt, "REFRESH_TOKEN_REUSE_GRACE_SECONDS", 0)
    payload = _base_payload()
    fake_redis.wall_clock = lambda: float(FROZEN_NOW)
    await fake_redis.set(
        AUTH_KEYS.refresh(str(payload["sub"]), str(payload["session_id"])),
        str(payload["jti"]),
        ex=config.jwt.REFRESH_TOKEN_EXPIRE_MINUTES * 60,
    )
    await rotate_session_tokens(payload, fake_redis, realm=USER_AUTH_REALM)

    invalidate_mock = AsyncMock()
    monkeypatch.setattr(token_helpers, "invalidate_all_sessions", invalidate_mock)

    with pytest.raises(UnauthorizedException, match="Token reuse detected"):
        await rotate_session_tokens(payload, fake_redis, realm=USER_AUTH_REALM)

    invalidate_mock.assert_awaited_once_with(payload["sub"], fake_redis, keys=AUTH_KEYS)


@pytest.mark.asyncio
async def test_rotate_session_tokens_reuse_detected(
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
        AUTH_KEYS.used(str(payload["sub"]), str(payload["jti"])),
        100,
        "used",
    )
    invalidate_mock = AsyncMock()
    monkeypatch.setattr(token_helpers, "invalidate_all_sessions", invalidate_mock)

    with pytest.raises(UnauthorizedException):
        await rotate_session_tokens(payload, fake_redis, realm=USER_AUTH_REALM)

    invalidate_mock.assert_awaited_once_with(payload["sub"], fake_redis, keys=AUTH_KEYS)


@pytest.mark.asyncio
async def test_rotate_session_tokens_invalid_state(
    fake_redis: InMemoryRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given: stored refresh jti does not match jti from payload.
    When: refresh token rotation is executed.
    Then: UnauthorizedException is raised and all sessions for the user are invalidated.
    """
    payload = _base_payload()
    await fake_redis.set(
        AUTH_KEYS.refresh(str(payload["sub"]), str(payload["session_id"])),
        "wrong-jti",
        ex=config.jwt.REFRESH_TOKEN_EXPIRE_MINUTES * 60,
    )
    invalidate_mock = AsyncMock()
    monkeypatch.setattr(token_helpers, "invalidate_all_sessions", invalidate_mock)

    with pytest.raises(UnauthorizedException):
        await rotate_session_tokens(payload, fake_redis, realm=USER_AUTH_REALM)

    invalidate_mock.assert_awaited_once_with(payload["sub"], fake_redis, keys=AUTH_KEYS)


@pytest.mark.asyncio
async def test_rotate_session_tokens_missing_jti_invalidates(
    fake_redis: InMemoryRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """
    Given: refresh payload misses jti field.
    When: refresh token rotation is executed.
    Then: UnauthorizedException is raised and all sessions for the user are invalidated.
    """
    invalidate_mock = AsyncMock()
    monkeypatch.setattr(token_helpers, "invalidate_all_sessions", invalidate_mock)

    payload = _base_payload()
    payload.pop("jti")

    with pytest.raises(UnauthorizedException):
        await rotate_session_tokens(payload, fake_redis, realm=USER_AUTH_REALM)

    invalidate_mock.assert_awaited_once_with(payload["sub"], fake_redis, keys=AUTH_KEYS)


async def _live_session(fake_redis: InMemoryRedis) -> JWTPayload:
    tokens = await issue_session_pair(
        realm=USER_AUTH_REALM,
        subject_id="user-id",
        claims={},
        redis_client=fake_redis,
        session_id="session-1",
    )
    return jwt.decode(
        tokens.refresh_token,
        config.jwt.JWT_USER_SECRET_KEY,
        algorithms=[config.jwt.ALGORITHM],
    )


async def _assert_dead(token: str, fake_redis: InMemoryRedis) -> None:
    with pytest.raises(UnauthorizedException):
        await verify_jti(token, fake_redis, realm=USER_AUTH_REALM)


@pytest.mark.asyncio
async def test_a_wipe_right_after_rotation_kills_the_new_pair(
    fake_redis: InMemoryRedis,
) -> None:
    """A password change that lands just after a refresh must still end the
    session the refresh produced - the new pair is indexed before the wipe reads."""
    payload = await _live_session(fake_redis)

    rotated = await rotate_session_tokens(payload, fake_redis, realm=USER_AUTH_REALM)
    await token_helpers.invalidate_all_sessions("user-id", fake_redis, keys=AUTH_KEYS)

    await _assert_dead(rotated.access_token, fake_redis)
    await _assert_dead(rotated.refresh_token, fake_redis)


@pytest.mark.asyncio
async def test_rotation_after_a_wipe_is_refused_and_registers_nothing(
    fake_redis: InMemoryRedis,
) -> None:
    payload = await _live_session(fake_redis)
    await token_helpers.invalidate_all_sessions("user-id", fake_redis, keys=AUTH_KEYS)

    with pytest.raises(UnauthorizedException):
        await rotate_session_tokens(payload, fake_redis, realm=USER_AUTH_REALM)

    assert await fake_redis.exists(AUTH_KEYS.refresh("user-id", "session-1")) == 0
    assert await fake_redis.exists(AUTH_KEYS.access("user-id", "session-1")) == 0
    assert await fake_redis.zrange(AUTH_KEYS.sessions("user-id"), 0, -1) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("rotation_first", [True, False])
async def test_a_wipe_racing_a_rotation_leaves_no_live_session(
    fake_redis: InMemoryRedis, rotation_first: bool
) -> None:
    """The wipe spans several round-trips; whichever way the two interleave,
    nothing the rotation wrote may survive it."""
    payload = await _live_session(fake_redis)
    rotation = rotate_session_tokens(payload, fake_redis, realm=USER_AUTH_REALM)
    wipe = token_helpers.invalidate_all_sessions("user-id", fake_redis, keys=AUTH_KEYS)

    results = await asyncio.gather(
        *((rotation, wipe) if rotation_first else (wipe, rotation)),
        return_exceptions=True,
    )

    for result in results:
        if isinstance(result, TokenModel):
            await _assert_dead(result.access_token, fake_redis)
            await _assert_dead(result.refresh_token, fake_redis)
    assert await fake_redis.exists(AUTH_KEYS.refresh("user-id", "session-1")) == 0
    assert await fake_redis.exists(AUTH_KEYS.access("user-id", "session-1")) == 0
