import pytest

from taskiq_worker import heartbeat
from taskiq_worker.heartbeat import (
    HEARTBEAT_TTL_SECONDS,
    HeartbeatScheduleSource,
    heartbeat_is_fresh,
    heartbeat_key,
)
from tests.fakes.redis import InMemoryRedis


@pytest.fixture
def redis_from_url(
    monkeypatch: pytest.MonkeyPatch, fake_redis: InMemoryRedis
) -> InMemoryRedis:
    monkeypatch.setattr(heartbeat.Redis, "from_url", lambda _url: fake_redis)
    monkeypatch.setattr(heartbeat.socket, "gethostname", lambda: "scheduler-a")
    return fake_redis


async def test_each_schedule_update_refreshes_the_heartbeat(
    redis_from_url: InMemoryRedis,
) -> None:
    source = HeartbeatScheduleSource("redis://unused")
    await source.startup()

    assert await source.get_schedules() == []
    assert await redis_from_url.ttl(heartbeat_key()) == HEARTBEAT_TTL_SECONDS
    assert await heartbeat_is_fresh("redis://unused")


async def test_a_scheduler_that_never_wrote_is_not_fresh(
    redis_from_url: InMemoryRedis,
) -> None:
    assert not await heartbeat_is_fresh("redis://unused")


async def test_a_predecessor_heartbeat_does_not_vouch_for_this_container(
    redis_from_url: InMemoryRedis,
) -> None:
    """A deploy replaces the scheduler while the old heartbeat is still fresh."""
    await redis_from_url.set(heartbeat_key("scheduler-old"), "alive", ex=60)

    assert not await heartbeat_is_fresh("redis://unused")
