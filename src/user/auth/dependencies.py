from dataclasses import dataclass
from typing import Literal, cast

from fastapi import Depends, Request, Security
from fastapi.security.api_key import APIKeyHeader
import jwt
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database.session import get_session
from src.core.errors.exceptions import UnauthorizedException
from src.core.redis.dependencies import get_redis_client
from src.main.config import config
from src.user.auth.jwt_payload_schema import JWTPayload
from src.user.auth.redis_keys import auth_redis_keys
from src.user.auth.token_helpers import invalidate_all_user_sessions
from src.user.dependencies import get_user_repository
from src.user.models import User
from src.user.repositories import UserRepository

access_token_header = APIKeyHeader(name="Authorization", scheme_name="access-token")
refresh_token_header = APIKeyHeader(name="Authorization", scheme_name="refresh-token")


@dataclass(frozen=True, slots=True)
class AuthenticatedUser:
    user: User
    session_id: str


async def get_current_user(
    token: str = Security(access_token_header),
    session: AsyncSession = Depends(get_session),
    redis_client: Redis = Depends(get_redis_client),
    user_repository: UserRepository = Depends(get_user_repository),
) -> User:
    """
    Resolve the authenticated user from a valid access token.

    Args:
        token: The JWT access token from the Authorization header.
        session: Database session.
        redis_client: Redis client used to validate the active token JTI.
        user_repository: Repository used to load the user entity.

    Returns:
        User: The authenticated user.

    Raises:
        UnauthorizedException: If the token is invalid, is not an access token,
            or the user cannot be loaded.
    """
    authenticated = await get_current_user_with_session(
        token=token,
        session=session,
        redis_client=redis_client,
        user_repository=user_repository,
    )
    return authenticated.user


async def get_current_user_with_session(
    token: str = Security(access_token_header),
    session: AsyncSession = Depends(get_session),
    redis_client: Redis = Depends(get_redis_client),
    user_repository: UserRepository = Depends(get_user_repository),
) -> AuthenticatedUser:
    """
    Resolve the authenticated user and current access-token session identifier.

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
        raise credentials_exception

    user = await user_repository.get_single(session, id=user_id)
    if not user:
        raise credentials_exception

    return AuthenticatedUser(user=user, session_id=session_id)


async def get_access_by_refresh_token(
    refresh_token: str = Security(refresh_token_header),
    session: AsyncSession = Depends(get_session),
    redis_client: Redis = Depends(get_redis_client),
    user_repository: UserRepository = Depends(get_user_repository),
) -> tuple[User, JWTPayload]:
    """
    Resolve the authenticated user and payload from a valid refresh token.

    Args:
        refresh_token: The JWT refresh token from the Authorization header.
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
    payload = await verify_jti(refresh_token, redis_client)

    try:
        user_id = payload["sub"]
        mode = payload["mode"]

        if mode != "refresh_token":
            raise credentials_exception

    except KeyError:
        raise credentials_exception

    user = await user_repository.get_single(session, id=user_id)
    if not user:
        raise credentials_exception

    return user, payload


async def get_user_id_from_token(
    request: Request,
) -> str:
    """
    Extract the user identifier from the Authorization header token.

    Args:
        request: The incoming request with the Authorization header.

    Returns:
        str: The authenticated user identifier from the verified token.

    Raises:
        UnauthorizedException: If the header is missing or the token is invalid.
    """

    token = request.headers.get("Authorization")

    if not token:
        raise UnauthorizedException(
            "Authentication token not found",
        )

    redis_client = await get_redis_client(request)
    payload = await verify_jti(token, redis_client)
    try:
        identifier = payload["sub"]

        return identifier
    except KeyError:
        raise UnauthorizedException(
            "Invalid or expired token",
        )


async def verify_jti(token: str, redis_client: Redis) -> JWTPayload:
    """
    Verify JWT claims and compare the token JTI against Redis state.

    Args:
        token: The JWT token, with or without the `Bearer ` prefix.
        redis_client: Redis client used to validate active and used keys.

    Returns:
        JWTPayload: The verified JWT payload.

    Raises:
        UnauthorizedException: If the token is expired, malformed, has an invalid
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
        raise UnauthorizedException("Token expired")
    except jwt.PyJWTError:
        raise UnauthorizedException("Invalid token")

    try:
        jti = payload_typed["jti"]
        mode = payload_typed["mode"]
        user_id = payload_typed["sub"]
        session_id = payload_typed["session_id"]
    except KeyError:
        raise UnauthorizedException("Invalid token structure")

    if mode not in {"access_token", "refresh_token"}:
        raise UnauthorizedException("Invalid token structure")
    session_mode = cast(Literal["access_token", "refresh_token"], mode)

    # Check for reuse
    if mode == "refresh_token":
        used_key = auth_redis_keys.used(user_id, jti)
        is_used = await redis_client.exists(used_key)

        if is_used:
            # Token reuse detected!
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
