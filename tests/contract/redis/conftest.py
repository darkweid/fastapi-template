from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import cast

import pytest
from redis.asyncio import Redis

from src.core.redis.core import create_redis_client
from src.main.config import config
from tests.contract.redis.backends import (
    REAL,
    REDIS_BACKENDS,
    require_throwaway_instance,
)
from tests.fakes.redis import InMemoryRedis

CONTRACT_ROOT = Path(__file__).resolve().parents[1]
REAL_ONLY_FIXTURES = frozenset({"real_redis", "redis_client_factory"})


def pytest_itemcollected(item: pytest.Item) -> None:
    """Mark every contract test that asks for the real instance directly.

    The backend param marks the `real` half of a parametrized scenario; a
    race scenario takes `real_redis` itself and has no param to carry the
    marker, so it is marked by the fixtures it requests, never by hand.
    """
    if item.path is None or CONTRACT_ROOT not in item.path.resolve().parents:
        return
    if REAL_ONLY_FIXTURES & set(getattr(item, "fixturenames", ())):
        item.add_marker(pytest.mark.integration)


@pytest.fixture
async def real_redis() -> AsyncIterator[Redis]:
    require_throwaway_instance(f"{config.redis.REDIS_HOST}:{config.redis.REDIS_PORT}")
    client = create_redis_client(config.redis.dsn)
    # FLUSHALL, not FLUSHDB: the receiver's claims live in the tasks database.
    await client.flushall()
    try:
        yield client
    finally:
        await client.flushall()
        await client.aclose()


@pytest.fixture
async def redis_client_factory(
    real_redis: Redis,
) -> AsyncIterator[Callable[[], Redis]]:
    """Clients with pools of their own, so racing callers arrive on separate
    connections the way separate app instances do."""
    clients: list[Redis] = []

    def build() -> Redis:
        client = create_redis_client(config.redis.dsn)
        clients.append(client)
        return client

    try:
        yield build
    finally:
        for client in clients:
            await client.aclose()


@pytest.fixture(params=REDIS_BACKENDS)
def redis_backend(request: pytest.FixtureRequest) -> Redis:
    # A sync fixture on purpose: getfixturevalue on the async real_redis from
    # inside a running event loop would fail.
    if request.param == REAL:
        return cast(Redis, request.getfixturevalue("real_redis"))
    return cast(Redis, InMemoryRedis())
