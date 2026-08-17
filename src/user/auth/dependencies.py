from dataclasses import dataclass
from typing import Annotated, Literal, cast

from fastapi import Depends, Request, Security
from fastapi.security.api_key import APIKeyHeader
import jwt
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database.session import get_session
from src.core.errors.exceptions import UnauthorizedException
from src.core.redis.dependencies import get_redis_client
from src.main.config import config
from src.user.auth.cookies import (
    CSRF_HEADER_NAME,
    REFRESH_COOKIE_NAME,
    TokenCookieResponder,
    get_token_cookie_responder,
)
from src.user.auth.errors import TokenExpiredError
from src.user.auth.jwt_payload_schema import JWTPayload
from src.user.auth.redis_keys import auth_redis_keys
from src.user.auth.token_helpers import (
    invalidate_all_user_sessions,
    is_within_reuse_grace,
)
from src.user.dependencies import get_user_repository
from src.user.models import User
from src.user.policies import ensure_can_use_session
from src.user.repositories import UserRepository

access_token_header = APIKeyHeader(name="Authorization", scheme_name="access-token")
# auto_error=False: a browser client authenticates with the refresh cookie and sends
# no Authorization header at all. The dependency below decides what is missing.
refresh_token_header = APIKeyHeader(
    name="Authorization", scheme_name="refresh-token", auto_error=False
)
# auto_error=False: logout accepts a request with no credentials at all, so that it
# can still clear the auth cookies. See get_logout_identity.
logout_token_header = APIKeyHeader(
    name="Authorization", scheme_name="logout-token", auto_error=False
)


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    user: User
    session_id: str


@dataclass(frozen=True, slots=True)
class SessionIdentity:
    """The user and session a token names, without loading the user entity."""

    user_id: str
    session_id: str


@dataclass(frozen=True, slots=True)
class RefreshCredentials:
    """A refresh token together with where it actually came from."""

    token: str
    from_cookie: bool


def read_refresh_credentials(request: Request) -> RefreshCredentials | None:
    """
    Locate the refresh token on a request: cookie first, Authorization header second.

    The source is a fact about the request, never a client claim. A caller must not
    be able to skip the CSRF check by declaring a body transport while still relying
    on the cookie the browser attached automatically.
    """
    cookie_token = request.cookies.get(REFRESH_COOKIE_NAME)
    if cookie_token:
        return RefreshCredentials(token=cookie_token, from_cookie=True)

    header_token = request.headers.get("Authorization")
    if header_token:
        return RefreshCredentials(token=header_token, from_cookie=False)

    return None


async def get_refresh_credentials(
    request: Request,
    header_token: Annotated[str | None, Security(refresh_token_header)] = None,
) -> RefreshCredentials:
    """
    Resolve the refresh token for the current request.

    header_token is declared only so that the security scheme still shows up in the
    OpenAPI document and Swagger keeps its authorize button; the actual lookup goes
    through read_refresh_credentials so that cookie and header follow one rule.
    """
    credentials = read_refresh_credentials(request)
    if credentials is None:
        raise UnauthorizedException("Could not validate credentials")

    return credentials


async def verify_csrf(
    request: Request,
    credentials: Annotated[RefreshCredentials, Depends(get_refresh_credentials)],
    responder: Annotated[TokenCookieResponder, Depends(get_token_cookie_responder)],
) -> None:
    """
    Enforce the CSRF double submit for cookie-borne refresh tokens.

    Skipped when the token arrived in the Authorization header: browsers do not
    attach that header to cross-site requests, so there is nothing to forge.
    """
    if not credentials.from_cookie:
        return

    responder.verify_csrf(credentials.token, request.headers.get(CSRF_HEADER_NAME))


async def authenticate_access_token(
    token: Annotated[str, Security(access_token_header)],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis_client: Annotated[Redis, Depends(get_redis_client)],
    user_repository: Annotated[UserRepository, Depends(get_user_repository)],
) -> AuthenticatedUser:
    """
    Resolve the token to a user and session, WITHOUT the admission gate.

    Args:
        token: The JWT access token from the Authorization header.
        session: Database session.
        redis_client: Redis client used to validate the active token JTI.
        user_repository: Repository used to load the user entity.

    Returns:
        AuthenticatedUser: The authenticated user and current session identifier.

    Raises:
        UnauthorizedException: If the token is invalid, is not an access token,
            or the user cannot be loaded.
    """
    credentials_exception = UnauthorizedException(
        "Could not validate credentials",
    )

    payload = await verify_jti(token, redis_client)

    try:
        user_id = payload["sub"]
        mode = payload["mode"]
        session_id = payload["session_id"]
        if mode != "access_token":
            raise credentials_exception
    except KeyError:
        raise credentials_exception from None

    user = await user_repository.get_single(session, id=user_id)
    if not user:
        raise credentials_exception

    return AuthenticatedUser(user=user, session_id=session_id)


async def get_current_user_with_session(
    authenticated: Annotated[AuthenticatedUser, Depends(authenticate_access_token)],
) -> AuthenticatedUser:
    """Authenticated AND admitted (active, verified) user with the session id."""
    ensure_can_use_session(authenticated.user)
    return authenticated


async def get_current_user(
    authenticated: Annotated[AuthenticatedUser, Depends(authenticate_access_token)],
) -> User:
    """The default auth dependency: authenticated AND admitted (active, verified).

    Blocked or unverified accounts answer 403 with an honest code here; the
    single opt-out is get_authenticated_user.
    """
    ensure_can_use_session(authenticated.user)
    return authenticated.user


async def get_authenticated_user(
    authenticated: Annotated[AuthenticatedUser, Depends(authenticate_access_token)],
) -> User:
    """Authentication without the admission gate.

    The single legitimate use is GET /me: a blocked or unverified account must
    still read its own state (is_verified) so the client can show the right
    screen. Any other use requires an explicit justification comment.
    """
    return authenticated.user


async def get_logout_identity(
    token: Annotated[str | None, Security(logout_token_header)] = None,
) -> SessionIdentity | None:
    """
    Identify the session a logout request asks to terminate.

    Unlike every other authenticated dependency this one tolerates an expired access
    token, and answers None instead of raising when it cannot identify a session.
    Logout has to stay usable once the access token expires: the refresh cookie is
    scoped to the refresh route and never reaches this endpoint, and a browser cannot
    drop an httponly cookie itself, so a rejected logout would leave the client
    holding a session it can neither use nor clear. The signature is still verified -
    only the `exp` claim is relaxed - so a forged token identifies nothing.

    Returns:
        SessionIdentity: The user and session named by a signature-valid access token.
        None: If no token was sent, or it is forged, malformed or not an access token.
    """
    if not token:
        return None

    if token.lower().startswith("bearer "):
        token = token[7:].strip()

    try:
        payload = cast(
            JWTPayload,
            jwt.decode(
                token,
                config.jwt.JWT_USER_SECRET_KEY,
                algorithms=[config.jwt.ALGORITHM],
                options={"verify_exp": False},
            ),
        )
    except jwt.PyJWTError:
        return None

    try:
        user_id = payload["sub"]
        session_id = payload["session_id"]
        mode = payload["mode"]
    except KeyError:
        return None

    if mode != "access_token":
        return None

    return SessionIdentity(user_id=user_id, session_id=session_id)


async def get_access_by_refresh_token(
    credentials: Annotated[RefreshCredentials, Depends(get_refresh_credentials)],
    _csrf: Annotated[None, Depends(verify_csrf)],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis_client: Annotated[Redis, Depends(get_redis_client)],
    user_repository: Annotated[UserRepository, Depends(get_user_repository)],
) -> tuple[User, JWTPayload]:
    """
    Resolve the authenticated user and payload from a valid refresh token.

    Args:
        credentials: The resolved refresh token and its source (cookie or header).
        _csrf: The CSRF gate that runs before the body; raises before this point
            if the token arrived by cookie without a valid CSRF header.
        session: Database session.
        redis_client: Redis client used to validate token state.
        user_repository: Repository used to load the user entity.

    Returns:
        tuple[User, JWTPayload]: The authenticated user and verified refresh payload.

    Raises:
        UnauthorizedException: If the token is invalid, is not a refresh token,
            or the user cannot be loaded.
    """
    credentials_exception = UnauthorizedException(
        "Could not validate credentials",
    )

    # verify_jti also validates the token and throws appropriate exceptions
    payload = await verify_jti(credentials.token, redis_client)

    try:
        user_id = payload["sub"]
        mode = payload["mode"]

        if mode != "refresh_token":
            raise credentials_exception

    except KeyError:
        raise credentials_exception from None

    user = await user_repository.get_single(session, id=user_id)
    if not user:
        raise credentials_exception

    return user, payload


async def get_user_id_from_token(
    request: Request,
) -> str:
    """
    Extract the user identifier from the refresh token, used as the rate limiter key.

    Looks at the refresh cookie first and the Authorization header second, following
    the same rule as read_refresh_credentials. On routes without a refresh cookie,
    only the header path applies.

    Args:
        request: The incoming request carrying the refresh cookie and/or header.

    Returns:
        str: The authenticated user identifier from the verified token.

    Raises:
        UnauthorizedException: If no credentials are found or the token is invalid.
    """
    credentials = read_refresh_credentials(request)
    if credentials is None:
        raise UnauthorizedException(
            "Authentication token not found",
        )

    redis_client = await get_redis_client(request)
    payload = await verify_jti(credentials.token, redis_client)
    try:
        identifier = payload["sub"]

        return identifier
    except KeyError:
        raise UnauthorizedException(
            "Invalid or expired token",
        ) from None


async def verify_jti(token: str, redis_client: Redis) -> JWTPayload:
    """
    Verify JWT claims and compare the token JTI against Redis state.

    Args:
        token: The JWT token, with or without the `Bearer ` prefix.
        redis_client: Redis client used to validate active and used keys.

    Returns:
        JWTPayload: The verified JWT payload.

    Raises:
        TokenExpiredError: If the token has expired.
        UnauthorizedException: If the token is malformed, has an invalid
            structure, was reused, or no longer matches the active Redis entry.
    """
    if isinstance(token, str) and token.lower().startswith("bearer "):
        token = token[7:].strip()

    try:
        payload = jwt.decode(
            token,
            config.jwt.JWT_USER_SECRET_KEY,
            algorithms=[config.jwt.ALGORITHM],
        )
        payload_typed = cast(JWTPayload, payload)
    except jwt.ExpiredSignatureError:
        raise TokenExpiredError("Token expired") from None
    except jwt.PyJWTError:
        raise UnauthorizedException("Invalid token") from None

    try:
        jti = payload_typed["jti"]
        mode = payload_typed["mode"]
        user_id = payload_typed["sub"]
        session_id = payload_typed["session_id"]
    except KeyError:
        raise UnauthorizedException("Invalid token structure") from None

    if mode not in {"access_token", "refresh_token"}:
        raise UnauthorizedException("Invalid token structure")
    session_mode = cast(Literal["access_token", "refresh_token"], mode)

    # Check for reuse
    if mode == "refresh_token":
        used_marker = await redis_client.get(auth_redis_keys.used(user_id, jti))

        if used_marker is not None:
            # Inside the grace window this is a benign double-submit, not
            # theft: reject the request but keep the session family alive.
            if await is_within_reuse_grace(used_marker, redis_client):
                raise UnauthorizedException("Token invalidated or expired")
            await invalidate_all_user_sessions(user_id, redis_client)
            raise UnauthorizedException(
                "Token reuse detected. All sessions invalidated."
            )

    # Check active tokens
    active_key = auth_redis_keys.session_key(session_mode, user_id, session_id)
    stored_jti = await redis_client.get(active_key)

    stored_jti_str = (
        stored_jti.decode()
        if isinstance(stored_jti, (bytes, bytearray))
        else stored_jti
    )

    if not stored_jti or stored_jti_str != jti:
        raise UnauthorizedException(
            "Token invalidated or expired",
        )

    return payload_typed
