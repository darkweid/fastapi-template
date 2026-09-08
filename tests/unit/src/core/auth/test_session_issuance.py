import pytest

from src.core.auth.session_issuance import LoginThrottle, issue_session_pair
from src.core.errors.exceptions import TooManyRequestsException
from tests.fakes.redis import InMemoryRedis
from tests.helpers.realm import build_test_realm

REALM = build_test_realm("throttle")


@pytest.mark.asyncio
async def test_issue_session_pair_returns_an_access_and_refresh_token(
    fake_redis: InMemoryRedis,
) -> None:
    tokens = await issue_session_pair(
        realm=REALM, subject_id="42", claims={}, redis_client=fake_redis
    )

    assert tokens.access_token
    assert tokens.refresh_token


@pytest.mark.asyncio
async def test_issue_session_pair_reuses_one_session_id_for_both_tokens(
    fake_redis: InMemoryRedis,
) -> None:
    await issue_session_pair(
        realm=REALM,
        subject_id="42",
        claims={},
        redis_client=fake_redis,
        session_id="s1",
    )

    assert await fake_redis.get(REALM.keys.access("42", "s1")) is not None
    assert await fake_redis.get(REALM.keys.refresh("42", "s1")) is not None


@pytest.mark.asyncio
async def test_ensure_under_limit_passes_below_the_limit(
    fake_redis: InMemoryRedis,
) -> None:
    throttle = LoginThrottle(REALM, limit=3, window_seconds=60)
    await fake_redis.setex(REALM.keys.login_failures("a@example.com"), 60, "2")

    await throttle.ensure_under_limit("a@example.com", fake_redis)


@pytest.mark.asyncio
async def test_ensure_under_limit_reports_the_remaining_ttl_as_retry_after(
    fake_redis: InMemoryRedis,
) -> None:
    throttle = LoginThrottle(REALM, limit=3, window_seconds=60)
    await fake_redis.setex(REALM.keys.login_failures("a@example.com"), 42, "3")

    with pytest.raises(TooManyRequestsException) as exc_info:
        await throttle.ensure_under_limit("a@example.com", fake_redis)

    assert exc_info.value.retry_after == 42


@pytest.mark.asyncio
async def test_ensure_under_limit_arms_a_ttl_less_counter_and_reports_the_full_window(
    fake_redis: InMemoryRedis,
) -> None:
    # A counter stranded without a TTL (a crash between INCR and EXPIRE) must
    # self-heal here - this gate rejects before record_failure ever runs again.
    key = REALM.keys.login_failures("a@example.com")
    throttle = LoginThrottle(REALM, limit=3, window_seconds=60)
    await fake_redis.set(key, "3")

    with pytest.raises(TooManyRequestsException) as exc_info:
        await throttle.ensure_under_limit("a@example.com", fake_redis)

    assert exc_info.value.retry_after == 60
    assert await fake_redis.ttl(key) == 60


@pytest.mark.asyncio
async def test_record_failure_arms_the_window_on_the_first_failure(
    fake_redis: InMemoryRedis,
) -> None:
    throttle = LoginThrottle(REALM, limit=3, window_seconds=60)
    key = REALM.keys.login_failures("a@example.com")

    await throttle.record_failure("a@example.com", fake_redis)

    assert await fake_redis.get(key) == "1"
    assert await fake_redis.ttl(key) == 60


@pytest.mark.asyncio
async def test_record_failure_never_pushes_an_existing_window_forward(
    fake_redis: InMemoryRedis,
) -> None:
    throttle = LoginThrottle(REALM, limit=3, window_seconds=60)
    key = REALM.keys.login_failures("a@example.com")
    await fake_redis.setex(key, 5, "1")

    await throttle.record_failure("a@example.com", fake_redis)

    assert await fake_redis.get(key) == "2"
    assert await fake_redis.ttl(key) == 5


@pytest.mark.asyncio
async def test_clear_removes_the_counter(fake_redis: InMemoryRedis) -> None:
    throttle = LoginThrottle(REALM, limit=3, window_seconds=60)
    key = REALM.keys.login_failures("a@example.com")
    await fake_redis.setex(key, 60, "2")

    await throttle.clear("a@example.com", fake_redis)

    assert await fake_redis.exists(key) == 0
