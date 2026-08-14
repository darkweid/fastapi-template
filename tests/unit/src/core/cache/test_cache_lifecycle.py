from collections.abc import Callable

from fastapi import FastAPI, Request, Response
import pytest

from src.core.cache.decorators import cached_route, validate_declared_ttls
from src.core.cache.dependencies import get_cache
from src.core.cache.interface import CacheKey, CacheScope
from src.core.cache.lifecycle import on_cache_shutdown, on_cache_startup
from src.core.cache.redis_cache import RedisCache
from src.core.cache.runtime import get_cache_instance, reset_cache
from src.main.config import config
from tests.fakes.redis import InMemoryRedis


@pytest.fixture(autouse=True)
def clean_runtime() -> None:
    reset_cache()
    yield
    reset_cache()


async def test_get_cache_instance_raises_before_startup() -> None:
    with pytest.raises(RuntimeError, match="not initialized"):
        get_cache_instance()


async def test_startup_binds_cache_to_app_redis_client() -> None:
    app = FastAPI()
    app.state.redis_client = InMemoryRedis()

    await on_cache_startup(app)

    assert isinstance(get_cache_instance(), RedisCache)
    assert await get_cache() is get_cache_instance()


async def test_shutdown_clears_instance() -> None:
    app = FastAPI()
    app.state.redis_client = InMemoryRedis()
    await on_cache_startup(app)

    await on_cache_shutdown()

    with pytest.raises(RuntimeError, match="not initialized"):
        get_cache_instance()


async def test_startup_without_redis_client_raises() -> None:
    with pytest.raises(RuntimeError, match="Redis client"):
        await on_cache_startup(FastAPI())


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
    app = FastAPI()
    app.state.redis_client = InMemoryRedis()
    declare_cached_route(60)
    monkeypatch.setattr(config.cache, "CACHE_VERSION_TTL", 30)

    with pytest.raises(ValueError, match="CACHE_VERSION_TTL"):
        await on_cache_startup(app)
