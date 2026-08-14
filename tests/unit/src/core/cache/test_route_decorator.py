from fastapi import FastAPI, Request, Response
import httpx2
import pytest

from src.core.cache.decorators import cached_route
from src.core.cache.interface import CacheKey, CacheScope
from src.core.cache.memory_cache import InMemoryCache
from src.core.cache.runtime import reset_cache, set_cache
from src.core.cache.serializer import JsonSerializer
from src.core.schemas import Base


class Summary(Base):
    name: str


def route_key(request: Request, identity_id: str | None) -> CacheKey:
    user_id = request.path_params["user_id"]
    suffix = "summary" if identity_id is None else f"summary:{identity_id}"
    return CacheKey(namespace=f"user:{user_id}", suffix=suffix)


async def constant_identity(request: Request) -> str | None:
    return request.headers.get("X-Test-User")


@pytest.fixture(autouse=True)
def runtime_cache() -> InMemoryCache:
    cache = InMemoryCache(
        serializer=JsonSerializer(), default_ttl=60, version_ttl=604800
    )
    set_cache(cache)
    yield cache
    reset_cache()


def build_app(counter: list[int], scope: CacheScope = CacheScope.PUBLIC) -> FastAPI:
    app = FastAPI()

    @app.get("/users/{user_id}", response_model=Summary)
    @cached_route(
        key_builder=route_key,
        ttl=60,
        scope=scope,
        identity=constant_identity if scope is CacheScope.PRIVATE else None,
    )
    async def read_user(
        user_id: str,
        request: Request,
        response: Response,
    ) -> Summary:
        counter.append(1)
        return Summary(name=f"user-{user_id}")

    return app


async def _client(app: FastAPI) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://testserver"
    )


async def test_miss_then_hit() -> None:
    counter: list[int] = []
    async with await _client(build_app(counter)) as client:
        first = await client.get("/users/1")
        second = await client.get("/users/1")

    assert first.headers["X-Cache-Status"] == "MISS"
    assert second.headers["X-Cache-Status"] == "HIT"
    assert second.json() == {"name": "user-1"}
    assert len(counter) == 1


async def test_etag_is_stable_and_if_none_match_returns_304() -> None:
    counter: list[int] = []
    async with await _client(build_app(counter)) as client:
        first = await client.get("/users/1")
        etag = first.headers["ETag"]
        second = await client.get("/users/1", headers={"If-None-Match": etag})

    assert second.status_code == 304
    assert second.content == b""


async def test_public_scope_sets_public_cache_control() -> None:
    async with await _client(build_app([])) as client:
        response = await client.get("/users/1")

    assert response.headers["Cache-Control"] == "public, max-age=60"


async def test_private_scope_separates_users_and_marks_response_private() -> None:
    counter: list[int] = []
    app = build_app(counter, scope=CacheScope.PRIVATE)
    async with await _client(app) as client:
        first = await client.get("/users/1", headers={"X-Test-User": "a"})
        second = await client.get("/users/1", headers={"X-Test-User": "b"})

    assert first.headers["Cache-Control"] == "private, max-age=60"
    assert second.headers["X-Cache-Status"] == "MISS"
    assert len(counter) == 2


def test_private_scope_without_identity_fails_at_import() -> None:
    with pytest.raises(ValueError, match="identity"):
        cached_route(key_builder=route_key, ttl=60, scope=CacheScope.PRIVATE)


def test_endpoint_without_request_parameter_fails_at_import() -> None:
    decorator = cached_route(key_builder=route_key, ttl=60, scope=CacheScope.PUBLIC)

    with pytest.raises(TypeError, match="Request"):

        @decorator
        async def endpoint(response: Response) -> Summary:
            return Summary(name="ada")


def test_endpoint_without_response_parameter_fails_at_import() -> None:
    decorator = cached_route(key_builder=route_key, ttl=60, scope=CacheScope.PUBLIC)

    with pytest.raises(TypeError, match="Response"):

        @decorator
        async def endpoint(request: Request) -> Summary:
            return Summary(name="ada")
