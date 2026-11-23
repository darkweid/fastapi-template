from collections.abc import Awaitable
from typing import cast

from redis.asyncio import Redis

from src.core.auth.realm import AuthRealm
from src.core.auth.redis_scripts import CONSUME_CHALLENGE_SCRIPT
from src.core.errors.exceptions import UnauthorizedException

INVALID_CHALLENGE_MESSAGE = "Invalid or expired token."


async def delete_if_value(key: str, value: str, redis_client: Redis) -> bool:
    """Delete the key only while it holds `value`; False when it does not."""
    verdict = await cast(
        Awaitable[str],
        redis_client.eval(
            CONSUME_CHALLENGE_SCRIPT,
            1,  # Number of keys
            key,
            value,
        ),
    )
    return verdict == "OK"


class ActiveChallengeRegistry:
    """Exactly one live challenge per realm, purpose and identifier, taken once.

    Holds whatever proves a challenge current - a token's jti for a signed
    link, the hash of a code for an OTP - so issuing a new challenge always
    retires the previous one. `consume` takes the challenge away in the same
    operation that checks it, so of any number of callers presenting one value
    exactly one gets through. A realm whose value is guessable must pair this
    with a failure counter of its own: a wrong value deliberately consumes
    nothing, so a short numeric code survives unlimited guesses at this layer.
    The identifier is hashed into the key, so an email or a phone number never
    reaches SCAN, MONITOR or an RDB dump.
    """

    def __init__(self, realm: AuthRealm) -> None:
        self._realm = realm

    async def store(
        self,
        purpose: str,
        identifier: str,
        value: str,
        ttl_seconds: int,
        redis_client: Redis,
        *,
        only_if_absent: bool = False,
    ) -> bool:
        """False only with `only_if_absent`, when another challenge is live: it
        stays, and `value` is not stored."""
        stored = await redis_client.set(
            self._realm.keys.one_time(purpose, identifier),
            value,
            ex=ttl_seconds,
            nx=only_if_absent,
        )
        return bool(stored)

    async def consume(
        self,
        purpose: str,
        identifier: str,
        value: str | None,
        redis_client: Redis,
    ) -> None:
        """Claim the live challenge for this caller, or raise.

        Returning means the challenge matched and is already gone: the caller
        must treat it as spent, because work that fails afterwards cannot hand
        it back. Every failure - missing, superseded, expired, or lost to
        another caller by a millisecond - answers the same error: telling a
        caller which one it was only helps an attacker.
        """
        if not value:
            raise UnauthorizedException(INVALID_CHALLENGE_MESSAGE)

        if not await delete_if_value(
            self._realm.keys.one_time(purpose, identifier), value, redis_client
        ):
            raise UnauthorizedException(INVALID_CHALLENGE_MESSAGE)

    async def invalidate(
        self, purpose: str, identifier: str, redis_client: Redis
    ) -> None:
        """Retire the challenge whatever its value, without claiming it.

        For a caller ending whatever is live - a deleted account, a changed
        password. One abandoning the challenge it stored itself calls `retire`.
        """
        await redis_client.delete(self._realm.keys.one_time(purpose, identifier))

    async def retire(
        self, purpose: str, identifier: str, value: str, redis_client: Redis
    ) -> None:
        """Retire the challenge only while it is still `value`: a newer one
        issued meanwhile stays live."""
        await delete_if_value(
            self._realm.keys.one_time(purpose, identifier), value, redis_client
        )
