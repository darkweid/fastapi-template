from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from unittest.mock import AsyncMock, Mock

import jwt
import pytest
from starlette.requests import Request as StarletteRequest

from src.core.auth import dependencies as core_auth_dependencies
from src.core.auth.cookies import CSRF_HEADER_NAME, TokenCookieResponder
from src.core.auth.credentials import (
    RefreshCredentials,
    read_refresh_credentials,
    verify_jti,
)
from src.core.auth.csrf import build_csrf_token
from src.core.auth.dependencies import Authenticated
from src.core.auth.errors import CsrfFailedError, TokenExpiredError
from src.core.auth.jwt_payload_schema import JWTPayload
from src.core.errors.codes import ErrorCode
from src.core.errors.exceptions import UnauthorizedException
from src.main.config import CookieConfig, config
from src.user.auth import dependencies
from src.user.auth.dependencies import (
    AuthenticatedUser,
    authenticate_access_token,
    get_access_by_refresh_token,
    get_authenticated_user,
    get_current_user,
    get_current_user_with_session,
    get_user_id_from_access_token,
    get_user_id_from_refresh_token,
    verify_csrf,
)
from src.user.auth.errors import UserBlockedError, UserNotVerifiedError
from src.user.auth.realm import USER_AUTH_REALM
from src.user.models import User
from src.user.repositories import UserRepository
from tests.factories.token_factory import (
    build_access_payload,
    build_refresh_payload,
    encode_access_payload,
)
from tests.factories.user_factory import build_user
from tests.fakes.db import FakeAsyncSession
from tests.fakes.redis import InMemoryRedis
from tests.helpers.requests import build_request

# Pinned wall clock for grace-window math; the fake's key expiry runs on
# time.monotonic(), so freezing this cannot make keys expire mid-test.
FROZEN_NOW = 1_755_000_000

AUTH_KEYS = USER_AUTH_REALM.keys
REFRESH_COOKIE_NAME = USER_AUTH_REALM.refresh_cookie


def encode_token(payload: dict[str, object], secret: str) -> str:
    return jwt.encode(payload, secret, config.jwt.ALGORITHM)


@pytest.mark.asyncio
async def test_verify_jti_accepts_bearer_prefix(fake_redis: InMemoryRedis) -> None:
    payload = build_access_payload("user-1")
    token = encode_token(payload, config.jwt.JWT_USER_SECRET_KEY)
    await fake_redis.set(
        AUTH_KEYS.access(payload["sub"], payload["session_id"]),
        payload["jti"],
        ex=60,
    )

    result = await verify_jti(f"Bearer {token}", fake_redis, realm=USER_AUTH_REALM)

    assert result["sub"] == "user-1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "claims",
    [
        {"aud": "another-product"},
        {"iss": "another-issuer"},
        {"aud": None},
        {"iss": None},
        {"iat": None},
        {"sub": None},
        {"exp": None},
        {"aud": ["another-product"]},
    ],
    ids=[
        "foreign-audience",
        "foreign-issuer",
        "no-audience",
        "no-issuer",
        "no-iat",
        "no-subject",
        "no-expiry",
        "audience-list-without-this-product",
    ],
)
async def test_verify_jti_refuses_a_token_not_issued_for_this_product(
    fake_redis: InMemoryRedis, claims: dict[str, object]
) -> None:
    """A product sharing the signing secret, or an identity service signing for
    several products, must not reach this one with a token meant elsewhere."""
    payload: dict[str, object] = {**build_access_payload("user-1"), **claims}
    payload = {key: value for key, value in payload.items() if value is not None}
    token = encode_token(payload, config.jwt.JWT_USER_SECRET_KEY)
    await fake_redis.set(
        AUTH_KEYS.access("user-1", str(payload["session_id"])),
        str(payload["jti"]),
        ex=60,
    )

    with pytest.raises(UnauthorizedException, match="Invalid token"):
        await verify_jti(token, fake_redis, realm=USER_AUTH_REALM)


@pytest.mark.asyncio
async def test_verify_jti_accepts_a_token_naming_several_audiences(
    fake_redis: InMemoryRedis,
) -> None:
    """A shared identity service lists every product a token is good for."""
    payload = {
        **build_access_payload("user-1"),
        "aud": ["another-product", config.jwt.JWT_AUDIENCE],
    }
    token = encode_token(payload, config.jwt.JWT_USER_SECRET_KEY)
    await fake_redis.set(
        AUTH_KEYS.access("user-1", str(payload["session_id"])),
        str(payload["jti"]),
        ex=60,
    )

    result = await verify_jti(token, fake_redis, realm=USER_AUTH_REALM)

    assert result["sub"] == "user-1"


@pytest.mark.asyncio
async def test_verify_jti_accepts_a_token_issued_a_moment_ahead_of_this_clock(
    fake_redis: InMemoryRedis,
) -> None:
    """Hosts behind one balancer drift by a second or two; a token one of them
    just issued must not answer 401 on another."""
    payload = build_access_payload("user-1")
    payload["iat"] = payload["iat"] + 10
    token = encode_token(dict(payload), config.jwt.JWT_USER_SECRET_KEY)
    await fake_redis.set(
        AUTH_KEYS.access("user-1", payload["session_id"]), payload["jti"], ex=60
    )

    result = await verify_jti(token, fake_redis, realm=USER_AUTH_REALM)

    assert result["sub"] == "user-1"


@pytest.mark.asyncio
async def test_get_logout_identity_ignores_a_token_without_an_expiry(
    fake_redis: InMemoryRedis,
) -> None:
    """Logout relaxes the expiry check, not the requirement that the claim exist:
    a token without `exp` would otherwise name a session forever."""
    await _live_logout_session(fake_redis)
    payload: dict[str, object] = dict(
        build_access_payload("user-1", session_id="session-1", jti=LIVE_ACCESS_JTI)
    )
    del payload["exp"]
    token = encode_token(payload, config.jwt.JWT_USER_SECRET_KEY)

    assert (
        await dependencies.get_logout_identity(redis_client=fake_redis, token=token)
        is None
    )


@pytest.mark.asyncio
async def test_get_logout_identity_ignores_a_token_for_another_product(
    fake_redis: InMemoryRedis,
) -> None:
    """Relaxing `exp` for logout must not relax the audience."""
    await _live_logout_session(fake_redis)
    payload = {
        **build_access_payload("user-1", session_id="session-1", jti=LIVE_ACCESS_JTI),
        "aud": "another-product",
    }
    token = encode_token(payload, config.jwt.JWT_USER_SECRET_KEY)

    assert (
        await dependencies.get_logout_identity(redis_client=fake_redis, token=token)
        is None
    )


@pytest.mark.asyncio
async def test_verify_jti_expired_token(fake_redis: InMemoryRedis) -> None:
    payload = build_access_payload("user-1")
    payload["exp"] = int((datetime.now(timezone.utc) - timedelta(hours=1)).timestamp())
    token = encode_token(payload, config.jwt.JWT_USER_SECRET_KEY)

    with pytest.raises(TokenExpiredError, match="Token expired") as exc_info:
        await verify_jti(token, fake_redis, realm=USER_AUTH_REALM)

    assert exc_info.value.error_code is ErrorCode.TOKEN_EXPIRED


@pytest.mark.asyncio
async def test_verify_jti_invalid_signature(fake_redis: InMemoryRedis) -> None:
    payload = build_access_payload("user-1")
    token = encode_token(payload, "wrong_secret_key_for_tests_more_than_32")

    with pytest.raises(UnauthorizedException, match="Invalid token"):
        await verify_jti(token, fake_redis, realm=USER_AUTH_REALM)


@pytest.mark.asyncio
async def test_verify_jti_invalid_structure(fake_redis: InMemoryRedis) -> None:
    payload = build_access_payload("user-1")
    payload.pop("jti")
    token = encode_token(payload, config.jwt.JWT_USER_SECRET_KEY)

    with pytest.raises(UnauthorizedException, match="Invalid token structure"):
        await verify_jti(token, fake_redis, realm=USER_AUTH_REALM)


def _used_marker(at: int, refresh_jti: str) -> str:
    return json.dumps(
        {
            "at": at,
            "refresh_jti": refresh_jti,
            "refresh_expires_at": at + 600,
            "access_jti": "successor-access",
            "access_expires_at": at + 60,
            "issued_at": at,
        }
    )


async def _rotated_refresh_token(
    fake_redis: InMemoryRedis, *, marker: str, live_refresh_jti: str
) -> tuple[JWTPayload, str]:
    payload = build_refresh_payload("user-1")
    await fake_redis.set(
        AUTH_KEYS.refresh(payload["sub"], payload["session_id"]),
        live_refresh_jti,
        ex=600,
    )
    await fake_redis.setex(AUTH_KEYS.used(payload["sub"], payload["jti"]), 600, marker)
    return payload, encode_token(payload, config.jwt.JWT_USER_SECRET_KEY)


@pytest.mark.asyncio
async def test_verify_jti_refresh_reuse_ends_the_session(
    fake_redis: InMemoryRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload, token = await _rotated_refresh_token(
        fake_redis, marker="used", live_refresh_jti="successor"
    )
    invalidate_mock = AsyncMock()
    monkeypatch.setattr("src.core.auth.credentials.invalidate_session", invalidate_mock)

    with pytest.raises(UnauthorizedException, match="Token reuse detected"):
        await verify_jti(token, fake_redis, realm=USER_AUTH_REALM)

    invalidate_mock.assert_awaited_once_with(
        payload["sub"], payload["session_id"], fake_redis, keys=AUTH_KEYS
    )


@pytest.mark.asyncio
async def test_verify_jti_lets_a_replay_of_an_unused_pair_through(
    fake_redis: InMemoryRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The client lost the rotation's answer: refusing here would sign it out
    # before rotation could hand it the same pair again.
    monkeypatch.setattr(config.jwt, "REFRESH_TOKEN_REUSE_GRACE_SECONDS", 30)
    fake_redis.wall_clock = lambda: float(FROZEN_NOW + 30)
    payload, token = await _rotated_refresh_token(
        fake_redis,
        marker=_used_marker(FROZEN_NOW, "successor"),
        live_refresh_jti="successor",
    )
    invalidate_mock = AsyncMock()
    monkeypatch.setattr("src.core.auth.credentials.invalidate_session", invalidate_mock)

    assert await verify_jti(token, fake_redis, realm=USER_AUTH_REALM) == payload
    invalidate_mock.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("now", "live_refresh_jti"),
    [
        pytest.param(FROZEN_NOW + 31, "successor", id="past-the-window"),
        pytest.param(FROZEN_NOW, "rotated-on", id="pair-already-used"),
    ],
)
async def test_verify_jti_rotated_out_token_is_reuse(
    fake_redis: InMemoryRedis,
    monkeypatch: pytest.MonkeyPatch,
    now: int,
    live_refresh_jti: str,
) -> None:
    monkeypatch.setattr(config.jwt, "REFRESH_TOKEN_REUSE_GRACE_SECONDS", 30)
    fake_redis.wall_clock = lambda: float(now)
    payload, token = await _rotated_refresh_token(
        fake_redis,
        marker=_used_marker(FROZEN_NOW, "successor"),
        live_refresh_jti=live_refresh_jti,
    )
    invalidate_mock = AsyncMock()
    monkeypatch.setattr("src.core.auth.credentials.invalidate_session", invalidate_mock)

    with pytest.raises(UnauthorizedException, match="Token reuse detected"):
        await verify_jti(token, fake_redis, realm=USER_AUTH_REALM)

    invalidate_mock.assert_awaited_once_with(
        payload["sub"], payload["session_id"], fake_redis, keys=AUTH_KEYS
    )


@pytest.mark.asyncio
async def test_verify_jti_active_token_mismatch(fake_redis: InMemoryRedis) -> None:
    payload = build_access_payload("user-1")
    token = encode_token(payload, config.jwt.JWT_USER_SECRET_KEY)
    await fake_redis.set(
        AUTH_KEYS.access(payload["sub"], payload["session_id"]),
        "other-jti",
        ex=60,
    )

    with pytest.raises(UnauthorizedException, match="Token invalidated"):
        await verify_jti(token, fake_redis, realm=USER_AUTH_REALM)


@pytest.mark.asyncio
async def test_authenticate_access_token_success(
    fake_redis: InMemoryRedis,
    fake_session: FakeAsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = build_user()
    payload = build_access_payload(str(user.id))
    token = encode_token(payload, config.jwt.JWT_USER_SECRET_KEY)
    await fake_redis.set(
        AUTH_KEYS.access(payload["sub"], payload["session_id"]),
        payload["jti"],
        ex=60,
    )
    get_single_mock = AsyncMock(return_value=user)
    monkeypatch.setattr(UserRepository, "get_single", get_single_mock)

    result = await authenticate_access_token(
        token=token,
        session=fake_session,
        redis_client=fake_redis,
    )

    assert isinstance(result, Authenticated)
    assert result.principal.id == user.id
    assert result.session_id == payload["session_id"]
    get_single_mock.assert_awaited_once_with(fake_session, id=str(user.id))


@pytest.mark.asyncio
async def test_authenticate_access_token_wrong_mode(
    fake_redis: InMemoryRedis, fake_session: FakeAsyncSession
) -> None:
    payload = build_refresh_payload("user-1")
    token = encode_token(payload, config.jwt.JWT_USER_SECRET_KEY)
    await fake_redis.set(
        AUTH_KEYS.refresh(payload["sub"], payload["session_id"]),
        payload["jti"],
        ex=60,
    )

    with pytest.raises(UnauthorizedException):
        await authenticate_access_token(
            token=token,
            session=fake_session,
            redis_client=fake_redis,
        )


@pytest.mark.asyncio
async def test_get_current_user_returns_user_when_admitted() -> None:
    user = build_user()
    authenticated = AuthenticatedUser(principal=user, session_id="sid")

    result = await get_current_user(authenticated)

    assert isinstance(result, User)
    assert result.id == user.id


@pytest.mark.asyncio
async def test_get_current_user_blocks_inactive_user() -> None:
    blocked = build_user(is_active=False)
    authenticated = AuthenticatedUser(principal=blocked, session_id="sid")

    with pytest.raises(UserBlockedError):
        await get_current_user(authenticated)


@pytest.mark.asyncio
async def test_get_current_user_blocks_unverified_user() -> None:
    unverified = build_user(is_verified=False)
    authenticated = AuthenticatedUser(principal=unverified, session_id="sid")

    with pytest.raises(UserNotVerifiedError):
        await get_current_user(authenticated)


@pytest.mark.asyncio
async def test_get_current_user_with_session_returns_when_admitted() -> None:
    user = build_user()
    authenticated = AuthenticatedUser(principal=user, session_id="sid")

    result = await get_current_user_with_session(authenticated)

    assert result is authenticated


@pytest.mark.asyncio
async def test_get_current_user_with_session_blocks_inactive_user() -> None:
    blocked = build_user(is_active=False)
    authenticated = AuthenticatedUser(principal=blocked, session_id="sid")

    with pytest.raises(UserBlockedError):
        await get_current_user_with_session(authenticated)


@pytest.mark.asyncio
async def test_get_authenticated_user_bypasses_the_gate_for_a_blocked_user() -> None:
    blocked = build_user(is_active=False)
    authenticated = AuthenticatedUser(principal=blocked, session_id="sid")

    result = await get_authenticated_user(authenticated)

    assert result is blocked


@pytest.mark.asyncio
async def test_get_authenticated_user_bypasses_the_gate_for_an_unverified_user() -> (
    None
):
    unverified = build_user(is_verified=False)
    authenticated = AuthenticatedUser(principal=unverified, session_id="sid")

    result = await get_authenticated_user(authenticated)

    assert result is unverified


@pytest.mark.asyncio
async def test_get_access_by_refresh_token_success(
    fake_redis: InMemoryRedis,
    fake_session: FakeAsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = build_user()
    payload = build_refresh_payload(str(user.id))
    token = encode_token(payload, config.jwt.JWT_USER_SECRET_KEY)
    await fake_redis.set(
        AUTH_KEYS.refresh(payload["sub"], payload["session_id"]),
        payload["jti"],
        ex=60,
    )
    get_single_mock = AsyncMock(return_value=user)
    monkeypatch.setattr(UserRepository, "get_single", get_single_mock)

    result_user, result_payload = await get_access_by_refresh_token(
        credentials=RefreshCredentials(token=token, from_cookie=False),
        _csrf=None,
        session=fake_session,
        redis_client=fake_redis,
    )

    assert result_user.id == user.id
    assert result_payload["mode"] == "refresh_token"
    get_single_mock.assert_awaited_once_with(
        fake_session,
        id=str(user.id),
    )


@pytest.mark.asyncio
async def test_get_user_id_from_refresh_token_missing_header(
    fake_redis: InMemoryRedis,
) -> None:
    request = build_request()

    with pytest.raises(UnauthorizedException, match="Authentication token not found"):
        await get_user_id_from_refresh_token(request)


@pytest.mark.asyncio
async def test_get_user_id_from_refresh_token_success(
    fake_redis: InMemoryRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = build_access_payload("user-1")
    token = encode_token(payload, config.jwt.JWT_USER_SECRET_KEY)
    await fake_redis.set(
        AUTH_KEYS.access(payload["sub"], payload["session_id"]),
        payload["jti"],
        ex=60,
    )
    request = build_request(headers={"Authorization": token})
    get_redis_mock = AsyncMock(return_value=fake_redis)
    monkeypatch.setattr(core_auth_dependencies, "get_redis_client", get_redis_mock)

    result = await get_user_id_from_refresh_token(request)

    assert result == "user-1"


@pytest.mark.asyncio
async def test_get_user_id_from_access_token_missing_header(
    fake_redis: InMemoryRedis,
) -> None:
    request = build_request()

    with pytest.raises(UnauthorizedException, match="Authentication token not found"):
        await get_user_id_from_access_token(request)


@pytest.mark.asyncio
async def test_get_user_id_from_access_token_rejects_a_refresh_token(
    fake_redis: InMemoryRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A realm signs both tokens with one secret, so without the mode check a
    refresh token would resolve here and let one principal hold two buckets."""
    payload = build_refresh_payload("user-1")
    token = encode_token(payload, config.jwt.JWT_USER_SECRET_KEY)
    await fake_redis.set(
        AUTH_KEYS.refresh(payload["sub"], payload["session_id"]),
        payload["jti"],
        ex=60,
    )
    request = build_request(headers={"Authorization": token})
    monkeypatch.setattr(
        core_auth_dependencies, "get_redis_client", AsyncMock(return_value=fake_redis)
    )

    with pytest.raises(UnauthorizedException):
        await get_user_id_from_access_token(request)


@pytest.mark.asyncio
async def test_get_user_id_from_access_token_success(
    fake_redis: InMemoryRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = build_access_payload("user-1")
    token = encode_token(payload, config.jwt.JWT_USER_SECRET_KEY)
    await fake_redis.set(
        AUTH_KEYS.access(payload["sub"], payload["session_id"]),
        payload["jti"],
        ex=60,
    )
    request = build_request(headers={"Authorization": token})
    monkeypatch.setattr(
        core_auth_dependencies, "get_redis_client", AsyncMock(return_value=fake_redis)
    )

    result = await get_user_id_from_access_token(request)

    assert result == "user-1"


def _request(
    *, cookie: str | None = None, authorization: str | None = None
) -> StarletteRequest:
    raw_headers = []
    if cookie is not None:
        raw_headers.append((b"cookie", f"{REFRESH_COOKIE_NAME}={cookie}".encode()))
    if authorization is not None:
        raw_headers.append((b"authorization", authorization.encode()))

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/users/auth/login/refresh",
        "headers": raw_headers,
    }
    return StarletteRequest(scope)


def test_cookie_is_preferred_over_the_header() -> None:
    credentials = read_refresh_credentials(
        _request(cookie="from-cookie", authorization="from-header"),
        realm=USER_AUTH_REALM,
    )

    assert credentials is not None
    assert credentials.token == "from-cookie"
    assert credentials.from_cookie is True


def test_header_is_used_when_there_is_no_cookie() -> None:
    credentials = read_refresh_credentials(
        _request(authorization="from-header"), realm=USER_AUTH_REALM
    )

    assert credentials is not None
    assert credentials.token == "from-header"
    assert credentials.from_cookie is False


def test_no_credentials_returns_none() -> None:
    assert read_refresh_credentials(_request(), realm=USER_AUTH_REALM) is None


CSRF_SECRET = "unit-test-csrf-secret-key-value-32"


def _refresh_responder() -> TokenCookieResponder:
    return TokenCookieResponder(
        realm=USER_AUTH_REALM,
        cookie_config=CookieConfig(CSRF_SECRET_KEY=CSRF_SECRET),
        refresh_token_expire_minutes=60,
    )


def _csrf_request(
    *,
    cookie: str | None = None,
    csrf_header: str | None = None,
    declared_transport: str | None = None,
) -> StarletteRequest:
    raw_headers = []
    if cookie is not None:
        raw_headers.append((b"cookie", f"{REFRESH_COOKIE_NAME}={cookie}".encode()))
    if csrf_header is not None:
        raw_headers.append((CSRF_HEADER_NAME.lower().encode(), csrf_header.encode()))
    if declared_transport is not None:
        raw_headers.append((b"x-token-transport", declared_transport.encode()))

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/users/auth/login/refresh",
        "headers": raw_headers,
    }
    return StarletteRequest(scope)


@pytest.mark.asyncio
async def test_verify_csrf_rejects_cookie_credentials_with_missing_csrf_token() -> None:
    request = _csrf_request(cookie="refresh-token-value")
    credentials = RefreshCredentials(token="refresh-token-value", from_cookie=True)

    with pytest.raises(CsrfFailedError):
        await verify_csrf(request, credentials, _refresh_responder())


@pytest.mark.asyncio
async def test_verify_csrf_rejects_cookie_credentials_with_wrong_csrf_token() -> None:
    request = _csrf_request(
        cookie="refresh-token-value", csrf_header="not-the-right-signature"
    )
    credentials = RefreshCredentials(token="refresh-token-value", from_cookie=True)

    with pytest.raises(CsrfFailedError):
        await verify_csrf(request, credentials, _refresh_responder())


@pytest.mark.asyncio
async def test_verify_csrf_accepts_cookie_credentials_with_valid_csrf_token() -> None:
    valid_signature = build_csrf_token("refresh-token-value", CSRF_SECRET)
    request = _csrf_request(cookie="refresh-token-value", csrf_header=valid_signature)
    credentials = RefreshCredentials(token="refresh-token-value", from_cookie=True)

    await verify_csrf(request, credentials, _refresh_responder())


@pytest.mark.asyncio
async def test_verify_csrf_skips_the_check_for_header_borne_credentials() -> None:
    request = _csrf_request()
    credentials = RefreshCredentials(token="refresh-token-value", from_cookie=False)
    responder = Mock(spec=TokenCookieResponder)

    await verify_csrf(request, credentials, responder)

    responder.verify_csrf.assert_not_called()


@pytest.mark.asyncio
async def test_verify_csrf_ignores_a_declared_body_transport_for_a_cookie_borne_token() -> (
    None
):
    """
    The adversarial case this task exists for: a client holding the refresh cookie
    declares X-Token-Transport: body to try to shed the CSRF check while still relying
    on the cookie the browser attaches automatically. The declared transport must not
    influence whether CSRF is enforced - only the actual token source does.
    """
    request = _csrf_request(cookie="refresh-token-value", declared_transport="body")
    credentials = RefreshCredentials(token="refresh-token-value", from_cookie=True)

    with pytest.raises(CsrfFailedError):
        await verify_csrf(request, credentials, _refresh_responder())


LIVE_ACCESS_JTI = "live-access-jti"


async def _live_logout_session(fake_redis: InMemoryRedis) -> None:
    await fake_redis.set(USER_AUTH_REALM.keys.refresh("user-1", "session-1"), "jti")
    await fake_redis.set(
        USER_AUTH_REALM.keys.latest_access("user-1", "session-1"), LIVE_ACCESS_JTI
    )


@pytest.mark.asyncio
async def test_get_logout_identity_accepts_an_expired_access_token(
    fake_redis: InMemoryRedis,
) -> None:
    """
    The reason this dependency exists: with cookie transport the refresh cookie is
    scoped to the refresh route and never reaches logout, and the browser cannot drop
    an httponly cookie itself. Rejecting an expired access token here would leave a
    user who logs out after access expiry holding a session they can neither use nor
    clear.
    """
    await _live_logout_session(fake_redis)
    payload = build_access_payload(
        "user-1",
        session_id="session-1",
        jti=LIVE_ACCESS_JTI,
        expires_in_minutes=-10,
    )
    token = encode_access_payload(payload)

    identity = await dependencies.get_logout_identity(
        redis_client=fake_redis, token=token
    )

    assert identity == dependencies.SessionIdentity(
        subject_id="user-1", session_id="session-1"
    )


@pytest.mark.asyncio
async def test_get_logout_identity_reads_a_bearer_prefixed_token(
    fake_redis: InMemoryRedis,
) -> None:
    await _live_logout_session(fake_redis)
    payload = build_access_payload(
        "user-1", session_id="session-1", jti=LIVE_ACCESS_JTI
    )
    token = encode_access_payload(payload)

    identity = await dependencies.get_logout_identity(
        redis_client=fake_redis, token=f"Bearer {token}"
    )

    assert identity == dependencies.SessionIdentity(
        subject_id="user-1", session_id="session-1"
    )


@pytest.mark.asyncio
async def test_get_logout_identity_returns_none_without_a_token(
    fake_redis: InMemoryRedis,
) -> None:
    assert (
        await dependencies.get_logout_identity(redis_client=fake_redis, token=None)
        is None
    )


@pytest.mark.asyncio
async def test_get_logout_identity_rejects_a_token_signed_with_another_key(
    fake_redis: InMemoryRedis,
) -> None:
    """Relaxing `exp` must not relax the signature: a forged token names no session."""
    await _live_logout_session(fake_redis)
    payload = build_access_payload(
        "user-1", session_id="session-1", jti=LIVE_ACCESS_JTI
    )
    forged = jwt.encode(payload, "x" * 32, config.jwt.ALGORITHM)

    assert (
        await dependencies.get_logout_identity(redis_client=fake_redis, token=forged)
        is None
    )


@pytest.mark.asyncio
async def test_get_logout_identity_rejects_a_refresh_token(
    fake_redis: InMemoryRedis,
) -> None:
    await _live_logout_session(fake_redis)
    payload = build_refresh_payload("user-1", session_id="session-1")
    token = jwt.encode(payload, config.jwt.JWT_USER_SECRET_KEY, config.jwt.ALGORITHM)

    assert (
        await dependencies.get_logout_identity(redis_client=fake_redis, token=token)
        is None
    )


@pytest.mark.asyncio
async def test_get_logout_identity_ignores_a_token_of_an_ended_session(
    fake_redis: InMemoryRedis,
) -> None:
    """The signature outlives the session: an old token of an ended session must
    not be able to order a wipe of the sessions the subject holds now."""
    payload = build_access_payload(
        "user-1", session_id="session-1", expires_in_minutes=-10
    )

    assert (
        await dependencies.get_logout_identity(
            redis_client=fake_redis, token=encode_access_payload(payload)
        )
        is None
    )


@pytest.mark.asyncio
async def test_get_logout_identity_ignores_an_access_token_rotated_out_of_a_live_session(
    fake_redis: InMemoryRedis,
) -> None:
    """A leaked access token a refresh has since replaced once logged the victim
    out of every session: the session was live, and nothing compared the jti."""
    await _live_logout_session(fake_redis)
    payload = build_access_payload(
        "user-1", session_id="session-1", jti="rotated-out-jti"
    )

    assert (
        await dependencies.get_logout_identity(
            redis_client=fake_redis, token=encode_access_payload(payload)
        )
        is None
    )


@pytest.mark.asyncio
async def test_get_logout_identity_still_ends_a_session_issued_before_latest_access(
    fake_redis: InMemoryRedis,
) -> None:
    """Sessions live when the key was introduced have none until their next
    refresh; logout must still revoke them rather than only clear cookies."""
    await fake_redis.set(USER_AUTH_REALM.keys.refresh("user-1", "session-1"), "jti")
    token = encode_access_payload(
        build_access_payload("user-1", session_id="session-1", jti="any-jti")
    )

    identity = await dependencies.get_logout_identity(
        redis_client=fake_redis, token=token
    )

    assert identity == dependencies.SessionIdentity(
        subject_id="user-1", session_id="session-1"
    )
