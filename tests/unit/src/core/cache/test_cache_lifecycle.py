from fastapi import FastAPI
import pytest

from src.core.cache.dependencies import get_cache
from src.core.cache.lifecycle import on_cache_shutdown, on_cache_startup
from src.core.cache.redis_cache import RedisCache
from src.core.cache.runtime import get_cache_instance, reset_cache
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
