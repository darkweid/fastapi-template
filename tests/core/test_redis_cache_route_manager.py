from __future__ import annotations

from fastapi import Response
import pytest

from src.core.redis.cache.coder.json_coder import JsonCoder
from src.core.redis.cache.manager.route_manager import RouteCacheManager
from tests.fakes.cache import FakeCacheBackend
from tests.helpers.requests import build_request


class RouteCounter:
    def __init__(self) -> None:
        self.calls = 0

    async def handle(self) -> dict[str, str]:
        self.calls += 1
        return {"status": "ok"}


ROUTE_COUNTER = RouteCounter()


async def route_handler() -> dict[str, str]:
    return await ROUTE_COUNTER.handle()


@pytest.mark.asyncio
async def test_route_cache_manager_cache_miss_sets_headers() -> None:
    backend = FakeCacheBackend()
    manager = RouteCacheManager(backend=backend, coder=JsonCoder())
    decorated = manager.decorator(ttl=10, tags=["tag"])(route_handler)
    request = build_request(path="/items", endpoint=route_handler)
    response = Response()

    ROUTE_COUNTER.calls = 0
    result = await decorated(
        __fastapi_cache_request=request,
        __fastapi_cache_response=response,
    )

    assert result == {"status": "ok"}
    assert ROUTE_COUNTER.calls == 1
    assert response.headers.get(manager.cache_status_header) == "MISS"
    assert response.headers.get(manager.cache_control_header) == "max-age=10"
    assert response.headers.get(manager.etag_header)


@pytest.mark.asyncio
async def test_route_cache_manager_hit_returns_304() -> None:
    backend = FakeCacheBackend()
    manager = RouteCacheManager(backend=backend, coder=JsonCoder())
    decorated = manager.decorator(ttl=10, tags=["tag"])(route_handler)
    request = build_request(path="/items", endpoint=route_handler)
    response = Response()

    ROUTE_COUNTER.calls = 0
    await decorated(
        __fastapi_cache_request=request,
        __fastapi_cache_response=response,
    )
    etag = response.headers.get(manager.etag_header)

    request_304 = build_request(
        path="/items",
        headers={manager.if_none_header: etag or ""},
        endpoint=route_handler,
    )
    response_304 = Response()
    result = await decorated(
        __fastapi_cache_request=request_304,
        __fastapi_cache_response=response_304,
    )

    assert ROUTE_COUNTER.calls == 1
    assert isinstance(result, Response)
    assert response_304.status_code == 304
    assert response_304.headers.get(manager.cache_status_header) == "HIT"
