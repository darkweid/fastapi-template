"""
Realm-agnostic request dependency factory.

`build_realm_auth` builds the whole FastAPI dependency set of one authentication
realm - resolving a token to a principal, gating admission, CSRF, refresh and
logout - from just a realm declaration, a principal repository and an admission
rule. A second class of principals (staff, partner, ...) costs one call to this
factory instead of a copy of `src/user/auth/dependencies.py`.
"""

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Annotated, Any, Generic, TypeVar

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
from src.core.auth.realm import AuthRealm
from src.core.database.base import Base
from src.core.database.repositories import BaseRepository
from src.core.database.session import get_session
from src.core.errors.exceptions import UnauthorizedException
from src.core.redis.dependencies import get_redis_client
from src.main.config import Config, get_settings

# Bound to the ORM base: every principal a realm authenticates (User, and any
# future realm's Staff/Partner) is a row loaded through a BaseRepository, never
# a plain value object.
PrincipalT = TypeVar("PrincipalT", bound=Base)


@dataclass(frozen=True, slots=True)
class Authenticated(Generic[PrincipalT]):
    """The principal a token names, together with the session it belongs to."""

    principal: PrincipalT
    session_id: str


@dataclass(frozen=True, slots=True)
class RealmAuth(Generic[PrincipalT]):
    """The FastAPI dependency set of one auth realm."""

    realm: AuthRealm
    cookie_responder: Callable[..., TokenCookieResponder]
    refresh_credentials: Callable[..., Coroutine[Any, Any, RefreshCredentials]]
    verify_csrf: Callable[..., Coroutine[Any, Any, None]]
    authenticate: Callable[..., Coroutine[Any, Any, Authenticated[PrincipalT]]]
    current_principal: Callable[..., Coroutine[Any, Any, PrincipalT]]
    current_principal_with_session: Callable[
        ..., Coroutine[Any, Any, Authenticated[PrincipalT]]
    ]
    authenticated_principal: Callable[..., Coroutine[Any, Any, PrincipalT]]
    logout_identity: Callable[..., Coroutine[Any, Any, SessionIdentity | None]]
    access_by_refresh: Callable[..., Coroutine[Any, Any, tuple[PrincipalT, JWTPayload]]]
    principal_id_from_token: Callable[..., Coroutine[Any, Any, str]]


def build_realm_auth(
    *,
    realm: AuthRealm,
    principal_type: type[PrincipalT],
    repository_factory: Callable[[], BaseRepository[PrincipalT]],
    admission: Callable[[PrincipalT], None],
) -> RealmAuth[PrincipalT]:
    """Build every request-scoped auth dependency of one realm.

    Repositories carry no state and take no constructor arguments, so the
    closures below build one themselves instead of resolving it through
    FastAPI - which is what lets these dependencies keep static signatures.

    `principal_type` is not read at runtime: it pins PrincipalT so that a
    caller's aliases type as the concrete entity rather than as Any.
    """
    access_header = APIKeyHeader(
        name="Authorization", scheme_name=f"{realm.name}-access-token"
    )
    # auto_error=False: a browser client authenticates with the refresh cookie and
    # sends no Authorization header at all. The dependency decides what is missing.
    refresh_header = APIKeyHeader(
        name="Authorization",
        scheme_name=f"{realm.name}-refresh-token",
        auto_error=False,
    )
    # auto_error=False: logout accepts a request with no credentials at all, so
    # that it can still clear the auth cookies.
    logout_header = APIKeyHeader(
        name="Authorization",
        scheme_name=f"{realm.name}-logout-token",
        auto_error=False,
    )
    credentials_error = "Could not validate credentials"

    def cookie_responder(
        settings: Annotated[Config, Depends(get_settings)],
    ) -> TokenCookieResponder:
        return TokenCookieResponder(
            realm=realm,
            cookie_config=settings.cookie,
            refresh_token_expire_minutes=settings.jwt.REFRESH_TOKEN_EXPIRE_MINUTES,
        )

    async def refresh_credentials(
        request: Request,
        header_token: Annotated[str | None, Security(refresh_header)] = None,
    ) -> RefreshCredentials:
        """Resolve the refresh token for the current request.

        header_token is declared only so the security scheme stays in the OpenAPI
        document and Swagger keeps its authorize button; the actual lookup goes
        through read_refresh_credentials so cookie and header follow one rule.
        """
        credentials = read_refresh_credentials(request, realm)
        if credentials is None:
            raise UnauthorizedException(credentials_error)
        return credentials

    async def verify_csrf(
        request: Request,
        credentials: Annotated[RefreshCredentials, Depends(refresh_credentials)],
        responder: Annotated[TokenCookieResponder, Depends(cookie_responder)],
    ) -> None:
        """Enforce the CSRF double submit for cookie-borne refresh tokens.

        Skipped when the token arrived in the Authorization header: browsers do
        not attach that header cross-site, so there is nothing to forge.
        """
        if not credentials.from_cookie:
            return
        responder.verify_csrf(credentials.token, request.headers.get(CSRF_HEADER_NAME))

    async def authenticate(
        token: Annotated[str, Security(access_header)],
        session: Annotated[AsyncSession, Depends(get_session)],
        redis_client: Annotated[Redis, Depends(get_redis_client)],
    ) -> Authenticated[PrincipalT]:
        """Resolve the access token to a principal, WITHOUT the admission gate."""
        payload = await verify_jti(token, redis_client, realm)
        try:
            subject_id = payload["sub"]
            mode = payload["mode"]
            session_id = payload["session_id"]
        except KeyError:
            raise UnauthorizedException(credentials_error) from None
        if mode != "access_token":
            raise UnauthorizedException(credentials_error)

        principal = await repository_factory().get_single(session, id=subject_id)
        if not principal:
            raise UnauthorizedException(credentials_error)

        return Authenticated(principal=principal, session_id=session_id)

    async def current_principal_with_session(
        authenticated: Annotated[Authenticated[PrincipalT], Depends(authenticate)],
    ) -> Authenticated[PrincipalT]:
        """Authenticated AND admitted principal, with the session id."""
        admission(authenticated.principal)
        return authenticated

    async def current_principal(
        authenticated: Annotated[Authenticated[PrincipalT], Depends(authenticate)],
    ) -> PrincipalT:
        """The default auth dependency: authenticated AND admitted."""
        admission(authenticated.principal)
        return authenticated.principal

    async def authenticated_principal(
        authenticated: Annotated[Authenticated[PrincipalT], Depends(authenticate)],
    ) -> PrincipalT:
        """Authentication without the admission gate.

        The single legitimate use is GET /me: a blocked or unverified account
        must still read its own state so the client can show the right screen.
        Any other use requires an explicit justification comment at the call site.
        """
        return authenticated.principal

    async def logout_identity(
        token: Annotated[str | None, Security(logout_header)] = None,
    ) -> SessionIdentity | None:
        """Identify the session a logout request asks to terminate.

        Unlike every other authenticated dependency this one tolerates an expired
        access token and answers None instead of raising. Logout has to stay
        usable once the access token expires: the refresh cookie is scoped to the
        refresh route and never reaches this endpoint, and a browser cannot drop
        an httponly cookie itself, so a rejected logout would leave the client
        holding a session it can neither use nor clear. The signature is still
        verified, so a forged token identifies nothing.
        """
        return await decode_logout_identity(token, realm)

    async def access_by_refresh(
        credentials: Annotated[RefreshCredentials, Depends(refresh_credentials)],
        _csrf: Annotated[None, Depends(verify_csrf)],
        session: Annotated[AsyncSession, Depends(get_session)],
        redis_client: Annotated[Redis, Depends(get_redis_client)],
    ) -> tuple[PrincipalT, JWTPayload]:
        """Resolve the principal and payload from a valid refresh token."""
        payload = await verify_jti(credentials.token, redis_client, realm)
        try:
            subject_id = payload["sub"]
            mode = payload["mode"]
        except KeyError:
            raise UnauthorizedException(credentials_error) from None
        if mode != "refresh_token":
            raise UnauthorizedException(credentials_error)

        principal = await repository_factory().get_single(session, id=subject_id)
        if not principal:
            raise UnauthorizedException(credentials_error)

        return principal, payload

    async def principal_id_from_token(request: Request) -> str:
        """Extract the subject id from the refresh token, for the rate limiter key."""
        credentials = read_refresh_credentials(request, realm)
        if credentials is None:
            raise UnauthorizedException("Authentication token not found")

        redis_client = await get_redis_client(request)
        payload = await verify_jti(credentials.token, redis_client, realm)
        try:
            return payload["sub"]
        except KeyError:
            raise UnauthorizedException("Invalid or expired token") from None

    return RealmAuth(
        realm=realm,
        cookie_responder=cookie_responder,
        refresh_credentials=refresh_credentials,
        verify_csrf=verify_csrf,
        authenticate=authenticate,
        current_principal=current_principal,
        current_principal_with_session=current_principal_with_session,
        authenticated_principal=authenticated_principal,
        logout_identity=logout_identity,
        access_by_refresh=access_by_refresh,
        principal_id_from_token=principal_id_from_token,
    )
