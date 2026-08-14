from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import FastAPI, Request, Response
import httpx2
from pydantic import ConfigDict, Field
import pytest

from src.core.cache.decorators import cached_route
from src.core.cache.interface import CacheKey, CacheScope
from src.core.cache.memory_cache import InMemoryCache
from src.core.cache.runtime import reset_cache, set_cache
from src.core.cache.serializer import JsonSerializer
from src.core.schemas import Base
from tests.unit.src.core.cache._string_annotated_endpoint import (
    StringAnnotatedSummary,
    read_user as string_annotated_read_user,
)


class Summary(Base):
    name: str


class AliasedSummary(Base):
    model_config = ConfigDict(populate_by_name=True)

    full_name: str = Field(alias="fullName")


def route_key(request: Request, identity_id: str | None) -> CacheKey:
    user_id = request.path_params["user_id"]
    suffix = "summary" if identity_id is None else f"summary:{identity_id}"
    return CacheKey(namespace=f"user:{user_id}", suffix=suffix)


async def constant_identity(request: Request) -> str | None:
    return request.headers.get("X-Test-User")


async def unresolved_identity(request: Request) -> str | None:
    return None


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


def build_identity_aware_app(
    counter: list[int],
    *,
    identity: Callable[[Request], Awaitable[str | None]],
) -> FastAPI:
    app = FastAPI()

    @app.get("/users/{user_id}", response_model=Summary)
    @cached_route(
        key_builder=route_key,
        ttl=60,
        scope=CacheScope.PRIVATE,
        identity=identity,
    )
    async def read_user(
        user_id: str,
        request: Request,
        response: Response,
    ) -> Summary:
        counter.append(1)
        caller = request.headers.get("X-Test-User", "anonymous")
        return Summary(name=f"user-{user_id}-as-{caller}")

    return app


def build_aliased_app(counter: list[int]) -> FastAPI:
    app = FastAPI()

    @app.get("/users/{user_id}", response_model=AliasedSummary)
    @cached_route(key_builder=route_key, ttl=60, scope=CacheScope.PUBLIC)
    async def read_user(
        user_id: str,
        request: Request,
        response: Response,
    ) -> AliasedSummary:
        counter.append(1)
        return AliasedSummary(full_name=f"user-{user_id}")

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


async def test_etag_is_stable_across_hits_and_304_matches_last_200() -> None:
    counter: list[int] = []
    async with await _client(build_app(counter)) as client:
        first = await client.get("/users/1")
        second = await client.get("/users/1")
        etag = first.headers["ETag"]
        third = await client.get("/users/1", headers={"If-None-Match": etag})

    assert first.headers["X-Cache-Status"] == "MISS"
    assert second.headers["X-Cache-Status"] == "HIT"
    assert first.headers["ETag"] == second.headers["ETag"]

    assert third.status_code == 304
    assert third.content == b""
    assert third.headers["ETag"] == second.headers["ETag"]
    assert third.headers["Cache-Control"] == second.headers["Cache-Control"]
    assert third.headers["X-Cache-Status"] == second.headers["X-Cache-Status"] == "HIT"


async def test_public_scope_sets_public_cache_control() -> None:
    async with await _client(build_app([])) as client:
        response = await client.get("/users/1")

    assert response.headers["Cache-Control"] == "public, max-age=60"


async def test_private_scope_separates_users_and_marks_response_private() -> None:
    counter: list[int] = []
    app = build_identity_aware_app(counter, identity=constant_identity)
    async with await _client(app) as client:
        alice = await client.get("/users/1", headers={"X-Test-User": "alice"})
        bob = await client.get("/users/1", headers={"X-Test-User": "bob"})

    assert alice.headers["Cache-Control"] == "private, max-age=60"
    assert alice.headers["X-Cache-Status"] == "MISS"
    assert alice.json() == {"name": "user-1-as-alice"}

    assert bob.headers["X-Cache-Status"] == "MISS"
    assert bob.json() == {"name": "user-1-as-bob"}
    assert bob.json() != alice.json()
    assert len(counter) == 2


async def test_private_scope_bypasses_cache_when_identity_resolves_to_none() -> None:
    counter: list[int] = []
    app = build_identity_aware_app(counter, identity=unresolved_identity)
    async with await _client(app) as client:
        alice = await client.get("/users/1", headers={"X-Test-User": "alice"})
        bob = await client.get("/users/1", headers={"X-Test-User": "bob"})

    assert alice.json() == {"name": "user-1-as-alice"}
    assert bob.json() == {"name": "user-1-as-bob"}
    assert bob.json() != alice.json()
    assert len(counter) == 2

    for response in (alice, bob):
        assert "X-Cache-Status" not in response.headers
        assert "ETag" not in response.headers
        assert "Cache-Control" not in response.headers


async def test_private_scope_without_identity_never_serves_a_304() -> None:
    counter: list[int] = []
    app = build_identity_aware_app(counter, identity=unresolved_identity)
    async with await _client(app) as client:
        first = await client.get("/users/1", headers={"X-Test-User": "alice"})
        replay = await client.get(
            "/users/1",
            headers={"X-Test-User": "alice", "If-None-Match": 'W/"anything"'},
        )

    assert replay.status_code == 200
    assert replay.json() == first.json()


async def test_response_model_with_aliased_field_does_not_500() -> None:
    counter: list[int] = []
    async with await _client(build_aliased_app(counter)) as client:
        first = await client.get("/users/1")
        second = await client.get("/users/1")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.headers["X-Cache-Status"] == "MISS"
    assert second.headers["X-Cache-Status"] == "HIT"
    assert first.json() == second.json() == {"fullName": "user-1"}
    assert len(counter) == 1


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


def test_decorated_endpoint_carries_the_cached_route_marker() -> None:
    decorator = cached_route(key_builder=route_key, ttl=60, scope=CacheScope.PUBLIC)

    async def endpoint(request: Request, response: Response) -> Summary:
        return Summary(name="ada")

    decorated = decorator(endpoint)

    assert getattr(decorated, "__cached_route__", False) is True
    assert not hasattr(endpoint, "__cached_route__")


def test_string_forward_ref_request_and_response_are_still_found() -> None:
    decorator = cached_route(key_builder=route_key, ttl=60, scope=CacheScope.PUBLIC)

    decorated = decorator(string_annotated_read_user)

    assert getattr(decorated, "__cached_route__", False) is True


def test_annotated_wrapped_request_and_response_are_still_found() -> None:
    decorator = cached_route(key_builder=route_key, ttl=60, scope=CacheScope.PUBLIC)

    async def endpoint(
        user_id: str,
        request: Annotated[Request, "unused metadata"],
        response: Annotated[Response, "unused metadata"],
    ) -> Summary:
        return Summary(name=f"user-{user_id}")

    decorated = decorator(endpoint)

    assert getattr(decorated, "__cached_route__", False) is True


async def test_string_forward_ref_endpoint_serves_through_a_real_app() -> None:
    app = FastAPI()
    app.get("/users/{user_id}", response_model=StringAnnotatedSummary)(
        cached_route(key_builder=route_key, ttl=60, scope=CacheScope.PUBLIC)(
            string_annotated_read_user
        )
    )

    async with await _client(app) as client:
        response = await client.get("/users/1")

    assert response.status_code == 200
    assert response.json() == {"name": "user-1"}
