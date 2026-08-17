from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, Mock

import jwt
import pytest
from starlette.requests import Request as StarletteRequest

from src.core.errors.codes import ErrorCode
from src.core.errors.exceptions import UnauthorizedException
from src.main.config import CookieConfig, config
from src.user.auth import dependencies
from src.user.auth.cookies import (
    CSRF_HEADER_NAME,
    REFRESH_COOKIE_NAME,
    TokenCookieResponder,
)
from src.user.auth.csrf import build_csrf_token
from src.user.auth.dependencies import (
    AuthenticatedUser,
    RefreshCredentials,
    authenticate_access_token,
    get_access_by_refresh_token,
    get_authenticated_user,
    get_current_user,
    get_current_user_with_session,
    get_user_id_from_token,
    read_refresh_credentials,
    verify_csrf,
    verify_jti,
)
from src.user.auth.errors import (
    CsrfFailedError,
    TokenExpiredError,
    UserBlockedError,
    UserNotVerifiedError,
)
from src.user.auth.redis_keys import auth_redis_keys
from src.user.models import User
from tests.factories.token_factory import (
    build_access_payload,
    build_refresh_payload,
    encode_access_payload,
)
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

    with pytest.raises(TokenExpiredError, match="Token expired") as exc_info:
        await verify_jti(token, fake_redis)

    assert exc_info.value.error_code is ErrorCode.TOKEN_EXPIRED


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
async def test_verify_jti_used_marker_within_grace_rejects_without_wipe(
    fake_redis: InMemoryRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A marker younger than the grace window is a benign double-submit, not an
    # attack: answer the generic 401 and leave the session family alone.
    monkeypatch.setattr(config.jwt, "REFRESH_TOKEN_REUSE_GRACE_SECONDS", 10)
    payload = build_refresh_payload("user-1")
    token = encode_token(payload, config.jwt.JWT_USER_SECRET_KEY)
    fake_redis.wall_clock = lambda: 1_755_000_000.0
    await fake_redis.setex(
        auth_redis_keys.used(payload["sub"], payload["jti"]),
        60,
        "1755000000",
    )
    invalidate_mock = AsyncMock()
    monkeypatch.setattr(
        "src.user.auth.dependencies.invalidate_all_user_sessions",
        invalidate_mock,
    )

    with pytest.raises(UnauthorizedException, match="Token invalidated or expired"):
        await verify_jti(token, fake_redis)

    invalidate_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_verify_jti_used_marker_past_grace_wipes(
    fake_redis: InMemoryRedis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config.jwt, "REFRESH_TOKEN_REUSE_GRACE_SECONDS", 10)
    payload = build_refresh_payload("user-1")
    token = encode_token(payload, config.jwt.JWT_USER_SECRET_KEY)
    fake_redis.wall_clock = lambda: 1_755_000_011.0
    await fake_redis.setex(
        auth_redis_keys.used(payload["sub"], payload["jti"]),
        60,
        "1755000000",
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
async def test_authenticate_access_token_success(
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

    result = await authenticate_access_token(
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
async def test_authenticate_access_token_wrong_mode(
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
        await authenticate_access_token(
            token=token,
            session=fake_session,
            redis_client=fake_redis,
            user_repository=FakeUserRepository(None),
        )


@pytest.mark.asyncio
async def test_get_current_user_returns_user_when_admitted() -> None:
    user = build_user()
    authenticated = AuthenticatedUser(user=user, session_id="sid")

    result = await get_current_user(authenticated)

    assert isinstance(result, User)
    assert result.id == user.id


@pytest.mark.asyncio
async def test_get_current_user_blocks_inactive_user() -> None:
    blocked = build_user(is_active=False)
    authenticated = AuthenticatedUser(user=blocked, session_id="sid")

    with pytest.raises(UserBlockedError):
        await get_current_user(authenticated)


@pytest.mark.asyncio
async def test_get_current_user_blocks_unverified_user() -> None:
    unverified = build_user(is_verified=False)
    authenticated = AuthenticatedUser(user=unverified, session_id="sid")

    with pytest.raises(UserNotVerifiedError):
        await get_current_user(authenticated)


@pytest.mark.asyncio
async def test_get_current_user_with_session_returns_when_admitted() -> None:
    user = build_user()
    authenticated = AuthenticatedUser(user=user, session_id="sid")

    result = await get_current_user_with_session(authenticated)

    assert result is authenticated


@pytest.mark.asyncio
async def test_get_current_user_with_session_blocks_inactive_user() -> None:
    blocked = build_user(is_active=False)
    authenticated = AuthenticatedUser(user=blocked, session_id="sid")

    with pytest.raises(UserBlockedError):
        await get_current_user_with_session(authenticated)


@pytest.mark.asyncio
async def test_get_authenticated_user_bypasses_the_gate_for_a_blocked_user() -> None:
    blocked = build_user(is_active=False)
    authenticated = AuthenticatedUser(user=blocked, session_id="sid")

    result = await get_authenticated_user(authenticated)

    assert result is blocked


@pytest.mark.asyncio
async def test_get_authenticated_user_bypasses_the_gate_for_an_unverified_user() -> (
    None
):
    unverified = build_user(is_verified=False)
    authenticated = AuthenticatedUser(user=unverified, session_id="sid")

    result = await get_authenticated_user(authenticated)

    assert result is unverified


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
        credentials=RefreshCredentials(token=token, from_cookie=False),
        _csrf=None,
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
        _request(cookie="from-cookie", authorization="from-header")
    )

    assert credentials is not None
    assert credentials.token == "from-cookie"
    assert credentials.from_cookie is True


def test_header_is_used_when_there_is_no_cookie() -> None:
    credentials = read_refresh_credentials(_request(authorization="from-header"))

    assert credentials is not None
    assert credentials.token == "from-header"
    assert credentials.from_cookie is False


def test_no_credentials_returns_none() -> None:
    assert read_refresh_credentials(_request()) is None


CSRF_SECRET = "unit-test-csrf-secret-key-value-32"


def _refresh_responder() -> TokenCookieResponder:
    return TokenCookieResponder(
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


@pytest.mark.asyncio
async def test_get_logout_identity_accepts_an_expired_access_token() -> None:
    """
    The reason this dependency exists: with cookie transport the refresh cookie is
    scoped to the refresh route and never reaches logout, and the browser cannot drop
    an httponly cookie itself. Rejecting an expired access token here would leave a
    user who logs out after access expiry holding a session they can neither use nor
    clear.
    """
    payload = build_access_payload(
        "user-1", session_id="session-1", expires_in_minutes=-10
    )
    token = encode_access_payload(payload)

    identity = await dependencies.get_logout_identity(token=token)

    assert identity == dependencies.SessionIdentity(
        user_id="user-1", session_id="session-1"
    )


@pytest.mark.asyncio
async def test_get_logout_identity_reads_a_bearer_prefixed_token() -> None:
    payload = build_access_payload("user-1", session_id="session-1")
    token = encode_access_payload(payload)

    identity = await dependencies.get_logout_identity(token=f"Bearer {token}")

    assert identity == dependencies.SessionIdentity(
        user_id="user-1", session_id="session-1"
    )


@pytest.mark.asyncio
async def test_get_logout_identity_returns_none_without_a_token() -> None:
    assert await dependencies.get_logout_identity(token=None) is None


@pytest.mark.asyncio
async def test_get_logout_identity_rejects_a_token_signed_with_another_key() -> None:
    """Relaxing `exp` must not relax the signature: a forged token names no session."""
    payload = build_access_payload("user-1", session_id="session-1")
    forged = jwt.encode(payload, "x" * 32, config.jwt.ALGORITHM)

    assert await dependencies.get_logout_identity(token=forged) is None


@pytest.mark.asyncio
async def test_get_logout_identity_rejects_a_refresh_token() -> None:
    payload = build_refresh_payload("user-1", session_id="session-1")
    token = jwt.encode(payload, config.jwt.JWT_USER_SECRET_KEY, config.jwt.ALGORITHM)

    assert await dependencies.get_logout_identity(token=token) is None
