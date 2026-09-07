from redis.asyncio import Redis

from src.core.auth.realm import AuthRealm
from src.core.errors.exceptions import UnauthorizedException

INVALID_CHALLENGE_MESSAGE = "Invalid or expired token."


class ActiveChallengeRegistry:
    """Exactly one live challenge per realm, purpose and identifier.

    Holds whatever proves a challenge current - a token's jti for a signed
    link, the hash of a code for an OTP - so issuing a new challenge always
    retires the previous one. The identifier is hashed into the key, so an
    email or a phone number never reaches SCAN, MONITOR or an RDB dump.
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
    ) -> None:
        await redis_client.set(
            self._realm.keys.one_time(purpose, identifier), value, ex=ttl_seconds
        )

    async def validate(
        self,
        purpose: str,
        identifier: str,
        value: str | None,
        redis_client: Redis,
    ) -> None:
        """Confirm the presented value is the live challenge.

        Every failure - missing, superseded, expired - answers the same error:
        telling a caller which one it was only helps an attacker.
        """
        if not value:
            raise UnauthorizedException(INVALID_CHALLENGE_MESSAGE)

        active = await redis_client.get(self._realm.keys.one_time(purpose, identifier))
        active_value = (
            active.decode() if isinstance(active, (bytes, bytearray)) else active
        )
        if active_value != value:
            raise UnauthorizedException(INVALID_CHALLENGE_MESSAGE)

    async def invalidate(
        self, purpose: str, identifier: str, redis_client: Redis
    ) -> None:
        await redis_client.delete(self._realm.keys.one_time(purpose, identifier))
