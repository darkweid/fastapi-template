from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request, Security
from fastapi.security.api_key import APIKeyHeader
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.auth.cookies import CSRF_HEADER_NAME, TokenCookieResponder
from src.core.auth.credentials import (
    RefreshCredentials,
    SessionIdentity,
    decode_logout_identity,
    read_refresh_credentials,
    verify_jti,
)
from src.core.auth.jwt_payload_schema import JWTPayload
from src.core.database.session import get_session
from src.core.errors.exceptions import UnauthorizedException
from src.core.redis.dependencies import get_redis_client
from src.main.config import Config, get_settings
from src.user.auth.realm import USER_AUTH_REALM
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


def get_token_cookie_responder(
    settings: Annotated[Config, Depends(get_settings)],
) -> TokenCookieResponder:
    """
    Temporary home for the user-realm cookie responder factory.

    A realm-generic factory belongs in a shared DI module once a second realm
    exists; until then this stays the one place that binds TokenCookieResponder
    to USER_AUTH_REALM.
    """
    return TokenCookieResponder(
        realm=USER_AUTH_REALM,
        cookie_config=settings.cookie,
        refresh_token_expire_minutes=settings.jwt.REFRESH_TOKEN_EXPIRE_MINUTES,
    )


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
    credentials = read_refresh_credentials(request, realm=USER_AUTH_REALM)
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

    payload = await verify_jti(token, redis_client, realm=USER_AUTH_REALM)

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

    Thin wrapper over decode_logout_identity, bound to the user realm; see that
    function's docstring for why an expired token is tolerated here.
    """
    return await decode_logout_identity(token, realm=USER_AUTH_REALM)


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

    payload = await verify_jti(credentials.token, redis_client, realm=USER_AUTH_REALM)

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
    credentials = read_refresh_credentials(request, realm=USER_AUTH_REALM)
    if credentials is None:
        raise UnauthorizedException(
            "Authentication token not found",
        )

    redis_client = await get_redis_client(request)
    payload = await verify_jti(credentials.token, redis_client, realm=USER_AUTH_REALM)
    try:
        identifier = payload["sub"]

        return identifier
    except KeyError:
        raise UnauthorizedException(
            "Invalid or expired token",
        ) from None
