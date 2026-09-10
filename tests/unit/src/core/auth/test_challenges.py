import asyncio

import pytest

from src.core.auth.challenges import ActiveChallengeRegistry
from src.core.auth.realm import AuthRealm
from src.core.errors.exceptions import UnauthorizedException

FIRST_REALM = AuthRealm(
    name="first",
    session_secret=lambda: "first-realm-secret-not-real-32-chars",
    refresh_cookie_path="/v1/first/auth/login/refresh",
)
SECOND_REALM = AuthRealm(
    name="second",
    session_secret=lambda: "second-realm-secret-not-real-32-char",
    refresh_cookie_path="/v1/second/auth/login/refresh",
)


async def test_the_stored_value_is_consumed(fake_redis: object) -> None:
    registry = ActiveChallengeRegistry(FIRST_REALM)

    await registry.store("verification", "person@example.com", "jti-1", 60, fake_redis)

    await registry.consume("verification", "person@example.com", "jti-1", fake_redis)


async def test_a_consumed_challenge_cannot_be_consumed_again(
    fake_redis: object,
) -> None:
    """Single use is the whole point: a spent link must stop working."""
    registry = ActiveChallengeRegistry(FIRST_REALM)
    await registry.store("verification", "person@example.com", "jti-1", 60, fake_redis)
    await registry.consume("verification", "person@example.com", "jti-1", fake_redis)

    with pytest.raises(UnauthorizedException):
        await registry.consume(
            "verification", "person@example.com", "jti-1", fake_redis
        )


async def test_only_one_of_two_concurrent_consumers_wins(fake_redis: object) -> None:
    """Two requests holding one code must not both be admitted.

    A check-then-delete registry passes the check in both before either deletes,
    which is how one OTP or one reset link logs in twice.
    """
    registry = ActiveChallengeRegistry(FIRST_REALM)
    await registry.store("verification", "person@example.com", "jti-1", 60, fake_redis)

    outcomes = await asyncio.gather(
        registry.consume("verification", "person@example.com", "jti-1", fake_redis),
        registry.consume("verification", "person@example.com", "jti-1", fake_redis),
        return_exceptions=True,
    )

    failures = [outcome for outcome in outcomes if isinstance(outcome, Exception)]
    assert len(failures) == 1
    assert isinstance(failures[0], UnauthorizedException)


async def test_a_superseded_value_is_rejected(fake_redis: object) -> None:
    registry = ActiveChallengeRegistry(FIRST_REALM)
    await registry.store("verification", "person@example.com", "jti-1", 60, fake_redis)

    await registry.store("verification", "person@example.com", "jti-2", 60, fake_redis)

    with pytest.raises(UnauthorizedException):
        await registry.consume(
            "verification", "person@example.com", "jti-1", fake_redis
        )


async def test_a_rejected_value_leaves_the_live_challenge_alone(
    fake_redis: object,
) -> None:
    """Consumption is a compare-and-delete, never a plain GETDEL.

    Deleting on a mismatch would let an old link retire the one its owner is
    waiting on - a hand-me-down denial of service on every issued challenge.
    """
    registry = ActiveChallengeRegistry(FIRST_REALM)
    await registry.store("verification", "person@example.com", "jti-2", 60, fake_redis)

    with pytest.raises(UnauthorizedException):
        await registry.consume(
            "verification", "person@example.com", "jti-1", fake_redis
        )

    await registry.consume("verification", "person@example.com", "jti-2", fake_redis)


async def test_a_missing_value_is_rejected(fake_redis: object) -> None:
    registry = ActiveChallengeRegistry(FIRST_REALM)
    await registry.store("verification", "person@example.com", "jti-1", 60, fake_redis)

    with pytest.raises(UnauthorizedException):
        await registry.consume("verification", "person@example.com", None, fake_redis)


async def test_invalidation_clears_the_challenge(fake_redis: object) -> None:
    registry = ActiveChallengeRegistry(FIRST_REALM)
    await registry.store("verification", "person@example.com", "jti-1", 60, fake_redis)

    await registry.invalidate("verification", "person@example.com", fake_redis)

    with pytest.raises(UnauthorizedException):
        await registry.consume(
            "verification", "person@example.com", "jti-1", fake_redis
        )


async def test_one_realm_cannot_clear_another_realm_challenge(
    fake_redis: object,
) -> None:
    first = ActiveChallengeRegistry(FIRST_REALM)
    second = ActiveChallengeRegistry(SECOND_REALM)
    await first.store("verification", "person@example.com", "jti-1", 60, fake_redis)
    await second.store("verification", "person@example.com", "jti-2", 60, fake_redis)

    await second.invalidate("verification", "person@example.com", fake_redis)

    await first.consume("verification", "person@example.com", "jti-1", fake_redis)


async def test_the_identifier_never_appears_in_the_key(fake_redis: object) -> None:
    registry = ActiveChallengeRegistry(FIRST_REALM)

    await registry.store("verification", "person@example.com", "jti-1", 60, fake_redis)

    assert all("person@example.com" not in key for key in fake_redis.keys_snapshot())
