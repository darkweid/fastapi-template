"""
Reading and verifying the credentials one auth realm's requests carry.

This is the request-facing counterpart to tokens.py: tokens.py issues and
rotates tokens, this module locates and verifies them on an incoming request.
Every function here is realm-parametrized so a second class of principals
reuses this logic by declaring a realm, not by copying it.
"""

from dataclasses import dataclass
from typing import cast

from fastapi import Request
import jwt
from redis.asyncio import Redis

from src.core.auth.errors import TokenExpiredError
from src.core.auth.jwt_payload_schema import JWTPayload
from src.core.auth.realm import AuthRealm
from src.core.auth.token_helpers import invalidate_all_sessions, is_within_reuse_grace
from src.core.errors.exceptions import UnauthorizedException
from src.main.config import config


@dataclass(frozen=True, slots=True)
class RefreshCredentials:
    """A refresh token together with where it actually came from."""

    token: str
    from_cookie: bool


@dataclass(frozen=True, slots=True)
class SessionIdentity:
    """The subject and session a token names, without loading the underlying entity."""

    subject_id: str
    session_id: str


def read_refresh_credentials(
    request: Request, realm: AuthRealm
) -> RefreshCredentials | None:
    """
    Locate the refresh token on a request: cookie first, Authorization header second.

    The source is a fact about the request, never a client claim. A caller must not
    be able to skip the CSRF check by declaring a body transport while still relying
    on the cookie the browser attached automatically.
    """
    cookie_token = request.cookies.get(realm.refresh_cookie)
    if cookie_token:
        return RefreshCredentials(token=cookie_token, from_cookie=True)

    header_token = request.headers.get("Authorization")
    if header_token:
        return RefreshCredentials(token=header_token, from_cookie=False)

    return None


async def verify_jti(token: str, redis_client: Redis, realm: AuthRealm) -> JWTPayload:
    """
    Verify JWT claims and compare the token JTI against Redis state.

    A valid signature is not enough: the jti must still be the one Redis holds
    for that session, so a token that was rotated out or revoked fails here
    while verifying cryptographically. Expiry raises TokenExpiredError, whose
    own error code lets a client tell "refresh and retry" apart from "this
    token is not ours"; everything else answers the generic 401.

    The `Bearer ` prefix is optional - callers hand over whatever the transport
    gave them, header or cookie.
    """
    if isinstance(token, str) and token.lower().startswith("bearer "):
        token = token[7:].strip()

    try:
        payload = jwt.decode(token, realm.secret, algorithms=[config.jwt.ALGORITHM])
        payload_typed = cast(JWTPayload, payload)
    except jwt.ExpiredSignatureError:
        raise TokenExpiredError("Token expired") from None
    except jwt.PyJWTError:
        raise UnauthorizedException("Invalid token") from None

    try:
        jti = payload_typed["jti"]
        mode = payload_typed["mode"]
        subject_id = payload_typed["sub"]
        session_id = payload_typed["session_id"]
    except KeyError:
        raise UnauthorizedException("Invalid token structure") from None

    if mode not in {"access_token", "refresh_token"}:
        raise UnauthorizedException("Invalid token structure")

    if mode == "refresh_token":
        used_marker = await redis_client.get(realm.keys.used(subject_id, jti))

        if used_marker is not None:
            # Inside the grace window this is a benign double-submit, not
            # theft: reject the request but keep the session family alive.
            if await is_within_reuse_grace(used_marker, redis_client):
                raise UnauthorizedException("Token invalidated or expired")
            await invalidate_all_sessions(subject_id, redis_client, keys=realm.keys)
            raise UnauthorizedException(
                "Token reuse detected. All sessions invalidated."
            )

    active_key = realm.keys.session_key(mode, subject_id, session_id)
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


async def decode_logout_identity(
    token: str | None, realm: AuthRealm
) -> SessionIdentity | None:
    """
    Identify the session a logout request asks to terminate.

    Unlike verify_jti this tolerates an expired token and answers None instead
    of raising when it cannot identify a session. Logout has to stay usable
    once the access token expires: the refresh cookie is scoped to the refresh
    route and never reaches logout, and a browser cannot drop an httponly
    cookie itself, so a rejected logout would leave the client holding a
    session it can neither use nor clear. The signature is still verified -
    only the `exp` claim is relaxed - so a forged token identifies nothing.

    Answers None for every unusable case alike: no token, a forged or malformed
    one, or one that is not an access token.
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
                realm.secret,
                algorithms=[config.jwt.ALGORITHM],
                options={"verify_exp": False},
            ),
        )
    except jwt.PyJWTError:
        return None

    try:
        subject_id = payload["sub"]
        session_id = payload["session_id"]
        mode = payload["mode"]
    except KeyError:
        return None

    if mode != "access_token":
        return None

    return SessionIdentity(subject_id=subject_id, session_id=session_id)
