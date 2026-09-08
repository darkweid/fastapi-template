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


async def test_the_stored_value_validates(fake_redis: object) -> None:
    registry = ActiveChallengeRegistry(FIRST_REALM)

    await registry.store("verification", "person@example.com", "jti-1", 60, fake_redis)

    await registry.validate("verification", "person@example.com", "jti-1", fake_redis)


async def test_a_superseded_value_is_rejected(fake_redis: object) -> None:
    registry = ActiveChallengeRegistry(FIRST_REALM)
    await registry.store("verification", "person@example.com", "jti-1", 60, fake_redis)

    await registry.store("verification", "person@example.com", "jti-2", 60, fake_redis)

    with pytest.raises(UnauthorizedException):
        await registry.validate(
            "verification", "person@example.com", "jti-1", fake_redis
        )


async def test_a_missing_value_is_rejected(fake_redis: object) -> None:
    registry = ActiveChallengeRegistry(FIRST_REALM)
    await registry.store("verification", "person@example.com", "jti-1", 60, fake_redis)

    with pytest.raises(UnauthorizedException):
        await registry.validate("verification", "person@example.com", None, fake_redis)


async def test_invalidation_clears_the_challenge(fake_redis: object) -> None:
    registry = ActiveChallengeRegistry(FIRST_REALM)
    await registry.store("verification", "person@example.com", "jti-1", 60, fake_redis)

    await registry.invalidate("verification", "person@example.com", fake_redis)

    with pytest.raises(UnauthorizedException):
        await registry.validate(
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

    await first.validate("verification", "person@example.com", "jti-1", fake_redis)


async def test_the_identifier_never_appears_in_the_key(fake_redis: object) -> None:
    registry = ActiveChallengeRegistry(FIRST_REALM)

    await registry.store("verification", "person@example.com", "jti-1", 60, fake_redis)

    assert all("person@example.com" not in key for key in fake_redis.keys_snapshot())
