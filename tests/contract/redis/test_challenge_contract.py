import asyncio
from collections.abc import Callable
from uuid import uuid4

import pytest
from redis.asyncio import Redis

from src.core.auth.challenges import ActiveChallengeRegistry
from src.core.errors.exceptions import UnauthorizedException
from src.user.auth.realm import USER_AUTH_REALM, VERIFICATION_PURPOSE

REGISTRY = ActiveChallengeRegistry(USER_AUTH_REALM)
TTL_SECONDS = 300


async def _issued(redis_client: Redis) -> tuple[str, str]:
    identifier = f"{uuid4().hex}@example.com"
    value = uuid4().hex
    await REGISTRY.store(
        VERIFICATION_PURPOSE, identifier, value, TTL_SECONDS, redis_client
    )
    return identifier, value


async def test_a_challenge_is_taken_once(redis_backend: Redis) -> None:
    """A verification link that works twice lets whoever saw it once act again."""
    identifier, value = await _issued(redis_backend)

    await REGISTRY.consume(VERIFICATION_PURPOSE, identifier, value, redis_backend)

    with pytest.raises(UnauthorizedException):
        await REGISTRY.consume(VERIFICATION_PURPOSE, identifier, value, redis_backend)
    key = USER_AUTH_REALM.keys.one_time(VERIFICATION_PURPOSE, identifier)
    assert await redis_backend.exists(key) == 0


async def test_a_wrong_value_consumes_nothing(redis_backend: Redis) -> None:
    """Anyone presenting a stale value must not retire the challenge the owner is
    still waiting on - that is why the script compares before it deletes."""
    identifier, value = await _issued(redis_backend)

    with pytest.raises(UnauthorizedException):
        await REGISTRY.consume(VERIFICATION_PURPOSE, identifier, "stale", redis_backend)

    await REGISTRY.consume(VERIFICATION_PURPOSE, identifier, value, redis_backend)


async def test_issuing_again_retires_the_previous_value(redis_backend: Redis) -> None:
    """Only the newest link a user received may work."""
    identifier, first = await _issued(redis_backend)
    second = uuid4().hex
    await REGISTRY.store(
        VERIFICATION_PURPOSE, identifier, second, TTL_SECONDS, redis_backend
    )

    with pytest.raises(UnauthorizedException):
        await REGISTRY.consume(VERIFICATION_PURPOSE, identifier, first, redis_backend)
    await REGISTRY.consume(VERIFICATION_PURPOSE, identifier, second, redis_backend)


async def test_a_challenge_expires_with_its_ttl(redis_backend: Redis) -> None:
    """The expiry is the only thing that ends an unused link."""
    identifier, _ = await _issued(redis_backend)

    key = USER_AUTH_REALM.keys.one_time(VERIFICATION_PURPOSE, identifier)
    assert 0 < await redis_backend.ttl(key) <= TTL_SECONDS


async def test_concurrent_consumes_let_exactly_one_through(
    real_redis: Redis, redis_client_factory: Callable[[], Redis]
) -> None:
    """Two requests holding one link must not both verify, reset or log in."""
    identifier, value = await _issued(real_redis)

    results = await asyncio.gather(
        *(
            REGISTRY.consume(
                VERIFICATION_PURPOSE, identifier, value, redis_client_factory()
            )
            for _ in range(8)
        ),
        return_exceptions=True,
    )

    assert sum(result is None for result in results) == 1
    assert all(
        isinstance(result, UnauthorizedException)
        for result in results
        if result is not None
    )
