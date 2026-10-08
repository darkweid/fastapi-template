from collections.abc import Callable
from contextlib import AsyncExitStack
from functools import partial
from unittest.mock import AsyncMock

from fastapi import Request, Response
from pydantic import SecretStr
import pytest

from src.core.cache.decorators import cached_route, validate_declared_ttls
from src.core.cache.interface import CacheKey, CacheScope
from src.core.cache.lifecycle import (
    on_cache_shutdown,
    on_cache_startup,
    open_cache_redis_client,
)
from src.core.cache.redis_cache import RedisCache
from src.core.cache.runtime import get_cache, get_cache_instance, reset_cache
from src.main.config import config
from tests.fakes.redis import InMemoryRedis

CACHE_URL = "redis://cache.internal:6379/0"


@pytest.fixture(autouse=True)
def clean_runtime() -> None:
    reset_cache()
    yield
    reset_cache()


async def test_get_cache_instance_raises_before_startup() -> None:
    with pytest.raises(RuntimeError, match="not initialized"):
        get_cache_instance()


async def test_startup_binds_cache_to_the_given_client() -> None:
    client = InMemoryRedis()

    await on_cache_startup(client)

    cache = get_cache_instance()
    assert isinstance(cache, RedisCache)
    assert cache._redis is client
    assert await get_cache() is cache


async def test_shutdown_clears_instance() -> None:
    await on_cache_startup(InMemoryRedis())

    await on_cache_shutdown()

    with pytest.raises(RuntimeError, match="not initialized"):
        get_cache_instance()


@pytest.fixture
def dedicated_cache_url(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Point CACHE_REDIS_URL at an instance and record every client built for it."""
    created_with: list[str] = []
    monkeypatch.setattr(config.cache, "CACHE_REDIS_URL", SecretStr(CACHE_URL))
    monkeypatch.setattr(
        "src.core.cache.lifecycle.create_redis_client",
        partial(_record_client, created_with),
    )
    return created_with


def _record_client(created_with: list[str], connection_url: str) -> InMemoryRedis:
    created_with.append(connection_url)
    return InMemoryRedis()


async def test_blank_cache_url_keeps_the_application_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(config.cache, "CACHE_REDIS_URL", SecretStr(""))
    application_client = InMemoryRedis()

    async with AsyncExitStack() as stack:
        client = await open_cache_redis_client(stack, application_client)

    assert client is application_client
    assert not application_client.closed


async def test_cache_url_opens_a_dedicated_client_closed_by_the_stack(
    dedicated_cache_url: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    verify = AsyncMock()
    monkeypatch.setattr("src.core.cache.lifecycle.verify_redis_client", verify)
    application_client = InMemoryRedis()

    async with AsyncExitStack() as stack:
        client = await open_cache_redis_client(stack, application_client)
        assert client is not application_client
        assert not client.closed

    assert dedicated_cache_url == [CACHE_URL]
    verify.assert_awaited_once_with(client)
    assert client.closed
    assert not application_client.closed


async def test_dedicated_client_is_closed_when_verify_fails(
    dedicated_cache_url: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # A wrong CACHE_REDIS_URL fails startup; the pool it opened must not leak.
    verify = AsyncMock(side_effect=RuntimeError("ping failed"))
    monkeypatch.setattr("src.core.cache.lifecycle.verify_redis_client", verify)

    with pytest.raises(RuntimeError, match="ping failed"):
        async with AsyncExitStack() as stack:
            await open_cache_redis_client(stack, InMemoryRedis())

    assert verify.await_args is not None
    assert verify.await_args.args[0].closed


async def test_disabled_cache_opens_no_dedicated_client(
    dedicated_cache_url: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    # A cache switched off never sends a command; an instance it still pinged
    # would fail startup over a service nothing uses.
    monkeypatch.setattr(config.cache, "CACHE_ENABLED", False)
    application_client = InMemoryRedis()

    async with AsyncExitStack() as stack:
        client = await open_cache_redis_client(stack, application_client)

    assert client is application_client
    assert dedicated_cache_url == []


@pytest.fixture
def declare_cached_route(monkeypatch: pytest.MonkeyPatch) -> Callable[[int], None]:
    # The registry is module state that decorating appends to, so each test gets
    # its own list - a probe route declared here must not follow the session.
    monkeypatch.setattr("src.core.cache.decorators._declared_ttls", [])

    def declare(ttl: int) -> None:
        @cached_route(
            key_builder=lambda request: CacheKey("probe", "entry"),
            ttl=ttl,
            scope=CacheScope.PUBLIC,
        )
        async def probe_endpoint(
            request: Request, response: Response
        ) -> dict[str, str]:
            return {}

    return declare


def test_declared_route_ttl_above_version_ttl_fails_validation(
    declare_cached_route: Callable[[int], None],
) -> None:
    # Decorating registers the ttl; the literal cannot be checked where it is
    # written, so this is the only place a route ttl of 60 under a 30s version ttl
    # can be caught - the alternative is a ValueError on every cache miss.
    declare_cached_route(60)

    with pytest.raises(ValueError, match="probe_endpoint"):
        validate_declared_ttls(30)


def test_declared_route_ttl_within_version_ttl_passes(
    declare_cached_route: Callable[[int], None],
) -> None:
    declare_cached_route(60)

    validate_declared_ttls(604800)


async def test_startup_rejects_a_version_ttl_below_a_declared_route_ttl(
    declare_cached_route: Callable[[int], None],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    declare_cached_route(60)
    monkeypatch.setattr(config.cache, "CACHE_VERSION_TTL", 30)

    with pytest.raises(ValueError, match="CACHE_VERSION_TTL"):
        await on_cache_startup(InMemoryRedis())
