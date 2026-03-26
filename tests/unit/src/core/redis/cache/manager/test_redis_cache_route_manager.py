from __future__ import annotations

import hashlib

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
    miss_etag = response.headers.get(manager.etag_header)

    request_304 = build_request(
        path="/items",
        headers={manager.if_none_header: miss_etag or ""},
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
    assert response_304.headers.get(manager.etag_header) == miss_etag


@pytest.mark.asyncio
async def test_route_cache_manager_generates_deterministic_etag() -> None:
    manager = RouteCacheManager(backend=FakeCacheBackend(), coder=JsonCoder())
    payload = JsonCoder.encode({"status": "ok"})

    etag = manager._build_etag(payload)

    assert etag == f'W/"{hashlib.sha256(payload).hexdigest()}"'


@pytest.mark.asyncio
async def test_route_cache_manager_if_none_match_uses_weak_etag_comparison() -> None:
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

    strong_etag = response.headers[manager.etag_header].replace("W/", "", 1)
    conditional_request = build_request(
        path="/items",
        headers={manager.if_none_header: strong_etag},
        endpoint=route_handler,
    )
    conditional_response = Response()
    result = await decorated(
        __fastapi_cache_request=conditional_request,
        __fastapi_cache_response=conditional_response,
    )

    assert ROUTE_COUNTER.calls == 1
    assert isinstance(result, Response)
    assert conditional_response.status_code == 304
