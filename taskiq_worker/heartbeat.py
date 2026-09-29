"""Scheduler liveness: a heartbeat the scheduler writes and its healthcheck reads.

Pinging Redis from the healthcheck proves only that Redis answers. A scheduler
whose loop has wedged, or that cannot write to the broker Redis, fires nothing
while that ping stays green. The scheduler loop asks every source for its
schedules once a minute (taskiq's `update_interval`), so a source that writes a
key on each ask is a heartbeat of the loop itself.

`python -m taskiq_worker.heartbeat` exits 0 while this container's heartbeat
is fresh. The key names the container (its hostname), so a scheduler that just
replaced another cannot pass on the heartbeat its predecessor left behind.
"""

import asyncio
import socket
import sys

from redis.asyncio import Redis
from taskiq import ScheduledTask
from taskiq.abc.schedule_source import ScheduleSource

from src.main.config import config

HEARTBEAT_KEY_PREFIX = "taskiq:scheduler:heartbeat"
# Two and a half of the scheduler's 60-second schedule updates: one late update
# is tolerated, two missed in a row are not.
HEARTBEAT_TTL_SECONDS = 150


def heartbeat_key(hostname: str | None = None) -> str:
    return f"{HEARTBEAT_KEY_PREFIX}:{hostname or socket.gethostname()}"


class HeartbeatScheduleSource(ScheduleSource):
    """Contributes no schedules; each ask for them refreshes the heartbeat.

    A failed write raises, which taskiq logs and treats as an empty list, so
    the heartbeat goes stale instead of the scheduler stopping.
    """

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._redis: Redis | None = None

    async def startup(self) -> None:
        self._redis = Redis.from_url(self._redis_url)

    async def shutdown(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()

    async def get_schedules(self) -> list[ScheduledTask]:
        if self._redis is None:
            raise RuntimeError("HeartbeatScheduleSource used before startup()")
        await self._redis.set(heartbeat_key(), "alive", ex=HEARTBEAT_TTL_SECONDS)
        return []


async def heartbeat_is_fresh(redis_url: str) -> bool:
    redis = Redis.from_url(redis_url)
    try:
        return bool(await redis.exists(heartbeat_key()))
    finally:
        await redis.aclose()


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(heartbeat_is_fresh(config.redis.tasks_dsn)) else 1)
