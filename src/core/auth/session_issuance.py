from typing import Any
from uuid import uuid4

from redis.asyncio import Redis

from src.core.auth.realm import AuthRealm
from src.core.auth.tokens import create_access_token, create_refresh_token
from src.core.errors.exceptions import TooManyRequestsException
from src.core.schemas import TokenModel


async def issue_session_pair(
    *,
    realm: AuthRealm,
    subject_id: str,
    claims: dict[str, Any],
    redis_client: Redis,
    session_id: str | None = None,
) -> TokenModel:
    """
    Issue an access and a refresh token belonging to one logical session.

    The session id is generated here rather than inside each token issuer, so
    the pair cannot drift apart: two ids would leave a refresh token that
    rotates a session its access token never belonged to.
    """
    session_id = session_id or str(uuid4())
    token_claims = {**claims, "sub": subject_id}

    access_token = await create_access_token(
        token_claims, redis_client, realm=realm, session_id=session_id
    )
    refresh_token = await create_refresh_token(
        token_claims, redis_client, realm=realm, session_id=session_id
    )
    return TokenModel(access_token=access_token, refresh_token=refresh_token)


class LoginThrottle:
    """Window-scoped counter of failed logins for one login identifier."""

    def __init__(self, realm: AuthRealm, limit: int, window_seconds: int) -> None:
        self._realm = realm
        self._limit = limit
        self._window_seconds = window_seconds

    async def ensure_under_limit(self, identifier: str, redis_client: Redis) -> None:
        """Reject before any database or password-hash work is spent."""
        key = self._realm.keys.login_failures(identifier)
        failures = await redis_client.get(key)
        if failures is None or int(failures) < self._limit:
            return

        retry_after = await redis_client.ttl(key)
        if retry_after < 0:
            # A counter stranded without a TTL (crash between INCR and EXPIRE)
            # never reaches record_failure again - this gate rejects first -
            # so the gate itself must arm the window, or the lockout is
            # permanent.
            await redis_client.expire(key, self._window_seconds, nx=True)
            retry_after = self._window_seconds
        raise TooManyRequestsException(
            "Too many failed login attempts.", retry_after=retry_after
        )

    async def record_failure(self, identifier: str, redis_client: Redis) -> None:
        key = self._realm.keys.login_failures(identifier)
        await redis_client.incr(key)
        # NX arms the window only when the key has no TTL yet, so later
        # failures never push the reset forward - and unlike an "only on the
        # first INCR" guard, a crash between INCR and EXPIRE cannot strand a
        # persistent counter: the next failure arms it.
        await redis_client.expire(key, self._window_seconds, nx=True)

    async def clear(self, identifier: str, redis_client: Redis) -> None:
        await redis_client.delete(self._realm.keys.login_failures(identifier))
