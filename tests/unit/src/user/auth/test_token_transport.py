from typing import Annotated
from unittest.mock import AsyncMock

from fastapi import Depends, FastAPI, routing
from fastapi.routing import APIRoute
from httpx2 import ASGITransport, AsyncClient
import pytest

from src.core.database.session import get_session
from src.core.redis.dependencies import get_redis_client
from src.core.schemas import TokenModel
from src.system import routers as system_routers
from src.user import routers as user_routers
from src.user.auth import routers as user_auth_routers
from src.user.auth.cookies import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    REFRESH_COOKIE_NAME,
    REFRESH_COOKIE_PATH,
)
from src.user.auth.dependencies import get_access_by_refresh_token
from src.user.auth.jwt_payload_schema import JWTPayload
from src.user.auth.token_transport import TokenTransport, get_token_transport
from src.user.auth.usecases.get_access_by_refresh import (
    get_tokens_by_refresh_user_use_case,
)
from src.user.auth.usecases.login import get_login_user_use_case
from src.user.dependencies import get_user_repository
from src.user.models import User
from tests.factories.token_factory import build_refresh_payload, build_refresh_token
from tests.factories.user_factory import build_user
from tests.fakes.db import FakeAsyncSession
from tests.fakes.redis import InMemoryRedis
from tests.helpers.limiter import noop_rate_limiter
from tests.helpers.overrides import DependencyOverrides
from tests.helpers.providers import ProvideAsyncValue, ProvideValue


class FakeUseCase:
    def __init__(self, result) -> None:
        self.execute = AsyncMock(return_value=result)


class FakeUserRepository:
    def __init__(self, user: User | None) -> None:
        self.get_single = AsyncMock(return_value=user)


@pytest.fixture(autouse=True)
def disable_rate_limiter(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "src.core.limiter.depends.RateLimiter.__call__",
        noop_rate_limiter,
    )


@pytest.fixture
def transport_app() -> FastAPI:
    app = FastAPI()

    @app.get("/probe")
    async def probe(
        transport: Annotated[TokenTransport, Depends(get_token_transport)],
    ) -> dict[str, str]:
        return {"transport": transport.value}

    return app


@pytest.mark.asyncio
async def test_missing_header_defaults_to_cookie(transport_app: FastAPI) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=transport_app), base_url="http://test"
    ) as client:
        response = await client.get("/probe")

    assert response.json() == {"transport": "cookie"}


@pytest.mark.asyncio
async def test_body_transport_is_selected_by_header(transport_app: FastAPI) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=transport_app), base_url="http://test"
    ) as client:
        response = await client.get("/probe", headers={"X-Token-Transport": "body"})

    assert response.json() == {"transport": "body"}


@pytest.mark.asyncio
async def test_unknown_transport_value_is_rejected(transport_app: FastAPI) -> None:
    async with AsyncClient(
        transport=ASGITransport(app=transport_app), base_url="http://test"
    ) as client:
        response = await client.get(
            "/probe", headers={"X-Token-Transport": "carrier-pigeon"}
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_login_defaults_to_cookie_transport(
    async_client,
    dependency_overrides: DependencyOverrides,
) -> None:
    tokens = TokenModel(access_token="a", refresh_token="r")
    dependency_overrides.set(get_login_user_use_case, ProvideValue(FakeUseCase(tokens)))

    response = await async_client.post(
        "/v1/users/auth/login",
        json={"email": "user@example.com", "password": "StrongPass1!"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["access_token"] == "a"
    assert body["refresh_token"] is None
    assert body["csrf_token"]

    set_cookie = response.headers.get_list("set-cookie")
    refresh_header = next(
        h for h in set_cookie if h.startswith(f"{REFRESH_COOKIE_NAME}=")
    )
    csrf_header = next(h for h in set_cookie if h.startswith(f"{CSRF_COOKIE_NAME}="))
    assert "HttpOnly" in refresh_header
    assert "HttpOnly" not in csrf_header


@pytest.mark.asyncio
async def test_login_with_body_transport_returns_both_tokens(
    async_client,
    dependency_overrides: DependencyOverrides,
) -> None:
    tokens = TokenModel(access_token="a", refresh_token="r")
    dependency_overrides.set(get_login_user_use_case, ProvideValue(FakeUseCase(tokens)))

    response = await async_client.post(
        "/v1/users/auth/login",
        json={"email": "user@example.com", "password": "StrongPass1!"},
        headers={"X-Token-Transport": "body"},
    )

    assert response.json() == {
        "access_token": "a",
        "refresh_token": "r",
        "csrf_token": None,
    }
    assert response.headers.get_list("set-cookie") == []


@pytest.mark.asyncio
async def test_refresh_from_cookie_without_csrf_header_is_forbidden(
    async_client,
) -> None:
    async_client.cookies.set(
        REFRESH_COOKIE_NAME, "cookie-refresh-token", path=REFRESH_COOKIE_PATH
    )

    response = await async_client.post("/v1/users/auth/login/refresh")

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_refresh_from_cookie_with_wrong_csrf_header_is_forbidden(
    async_client,
) -> None:
    async_client.cookies.set(
        REFRESH_COOKIE_NAME, "cookie-refresh-token", path=REFRESH_COOKIE_PATH
    )

    response = await async_client.post(
        "/v1/users/auth/login/refresh",
        headers={CSRF_HEADER_NAME: "not-the-right-signature"},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_declared_body_transport_does_not_disable_the_csrf_check(
    async_client,
) -> None:
    async_client.cookies.set(
        REFRESH_COOKIE_NAME, "cookie-refresh-token", path=REFRESH_COOKIE_PATH
    )

    response = await async_client.post(
        "/v1/users/auth/login/refresh",
        headers={"X-Token-Transport": "body"},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_refresh_with_body_transport_returns_both_tokens_and_sets_no_cookie(
    async_client,
    dependency_overrides: DependencyOverrides,
) -> None:
    # get_access_by_refresh_token is overridden wholesale here, so this does not
    # exercise the CSRF branch at all - it only proves that a body-transport refresh
    # returns both tokens and never touches the response cookies.
    user = build_user()
    payload: JWTPayload = build_refresh_payload(str(user.id))
    dependency_overrides.set(get_access_by_refresh_token, ProvideValue((user, payload)))
    tokens = TokenModel(access_token="a2", refresh_token="r2")
    dependency_overrides.set(
        get_tokens_by_refresh_user_use_case, ProvideValue(FakeUseCase(tokens))
    )

    response = await async_client.post(
        "/v1/users/auth/login/refresh",
        headers={"X-Token-Transport": "body", "Authorization": "header-refresh-token"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "access_token": "a2",
        "refresh_token": "r2",
        "csrf_token": None,
    }
    assert response.headers.get_list("set-cookie") == []


@pytest.mark.asyncio
async def test_login_refresh_via_cookie_and_csrf_header_succeeds(
    async_client,
    dependency_overrides: DependencyOverrides,
    fake_redis: InMemoryRedis,
    fake_session: FakeAsyncSession,
) -> None:
    """
    Drives the assembled default path end to end with nothing stubbed out below the
    use case boundary: login sets the refresh + csrf cookies, the client returns the
    refresh cookie automatically and replays the csrf cookie value as the CSRF
    header, and the real get_access_by_refresh_token / verify_csrf dependencies
    decide the outcome. Only the use cases and the repository/session/redis
    infrastructure are faked - the CSRF and credential-resolution logic under test
    runs unmodified.
    """
    user = build_user()
    refresh_token = await build_refresh_token({"sub": str(user.id)}, fake_redis)
    login_tokens = TokenModel(access_token="access-1", refresh_token=refresh_token)
    refreshed_tokens = TokenModel(access_token="access-2", refresh_token="refresh-2")

    dependency_overrides.set(
        get_login_user_use_case, ProvideValue(FakeUseCase(login_tokens))
    )
    dependency_overrides.set(
        get_tokens_by_refresh_user_use_case, ProvideValue(FakeUseCase(refreshed_tokens))
    )
    dependency_overrides.set(get_redis_client, ProvideValue(fake_redis))
    dependency_overrides.set(get_session, ProvideAsyncValue(fake_session))
    dependency_overrides.set(
        get_user_repository, ProvideValue(FakeUserRepository(user))
    )

    login_response = await async_client.post(
        "/v1/users/auth/login",
        json={"email": "user@example.com", "password": "StrongPass1!"},
    )

    assert login_response.status_code == 200
    login_body = login_response.json()
    assert login_body["access_token"] == "access-1"
    assert login_body["refresh_token"] is None

    # Read the CSRF token from the response body, the way a real cross-origin SPA
    # must. Reading it from `async_client.cookies` would prove nothing about client
    # reachability: an httpx2 jar has no notion of a current document, so it hands
    # back cookies a browser would refuse to expose to JS.
    csrf_token = login_body["csrf_token"]
    assert csrf_token

    refresh_response = await async_client.post(
        "/v1/users/auth/login/refresh",
        headers={CSRF_HEADER_NAME: csrf_token},
    )

    assert refresh_response.status_code == 200
    refresh_body = refresh_response.json()
    assert refresh_body["access_token"] == "access-2"
    assert refresh_body["refresh_token"] is None
    assert refresh_body["csrf_token"]

    refresh_set_cookie = refresh_response.headers.get_list("set-cookie")
    assert any(h.startswith(f"{REFRESH_COOKIE_NAME}=") for h in refresh_set_cookie)
    assert any(h.startswith(f"{CSRF_COOKIE_NAME}=") for h in refresh_set_cookie)


def _declares_transport(dependant) -> bool:
    if dependant.call is get_token_transport:
        return True
    return any(_declares_transport(sub) for sub in dependant.dependencies)


def test_refresh_cookie_path_matches_the_mounted_route(app) -> None:
    # `app.routes` nests included routers behind `_IncludedRouter` wrappers, so the
    # mounted path (with prefix applied) is only visible through the effective route
    # contexts FastAPI itself walks to build the OpenAPI schema.
    # NOTE: `fastapi.routing.iter_route_contexts` is a FastAPI *internal* API with no
    # stability guarantee. If this test (or the one below) breaks after a FastAPI
    # upgrade with an ImportError or AttributeError, that is the cause - the assertion
    # itself is still what we want, only the traversal helper needs replacing.
    refresh_paths = [
        route_context.path
        for route_context in routing.iter_route_contexts(app.routes)
        if isinstance(route_context.original_route, APIRoute)
        and route_context.path is not None
        and route_context.path.endswith("/auth/login/refresh")
    ]

    assert REFRESH_COOKIE_PATH in refresh_paths


def test_every_token_route_declares_the_transport_dependency(app) -> None:
    token_routes = [
        route_context
        for route_context in routing.iter_route_contexts(app.routes)
        if isinstance(route_context.original_route, APIRoute)
        and route_context.response_model is TokenModel
    ]

    assert token_routes, "No route returns TokenModel - the walker would pass vacuously"
    for route_context in token_routes:
        assert _declares_transport(
            route_context.dependant
        ), f"{route_context.path} bypasses the responder"


def test_cached_route_decorator_order_is_preserved(app: FastAPI) -> None:
    # `functools.wraps` copies `__name__`, so a wrapper produced by @cached_route is
    # indistinguishable from the undecorated function by name alone - that is why
    # @cached_route stamps a `__cached_route__` marker on the object it returns
    # instead. When the decorator order is correct (@cached_route inside
    # @router.get), the marked wrapper is both the module attribute and the
    # registered route endpoint. Invert the order and @router.get registers the
    # undecorated function while @cached_route's marked wrapper only reaches the
    # module attribute - this walk catches exactly that mismatch.
    # Scans every module that defines a router, not just src.user.routers, so an
    # inversion introduced in a future router module cannot pass silently.
    router_modules = (user_routers, user_auth_routers, system_routers)
    marked_endpoints = [
        obj
        for module in router_modules
        for obj in vars(module).values()
        if callable(obj) and getattr(obj, "__cached_route__", False)
    ]
    assert marked_endpoints, "expected at least one @cached_route-decorated endpoint"

    registered_endpoints = {
        route_context.original_route.endpoint
        for route_context in routing.iter_route_contexts(app.routes)
        if isinstance(route_context.original_route, APIRoute)
    }

    for endpoint in marked_endpoints:
        assert endpoint in registered_endpoints, (
            f"{endpoint.__qualname__} carries __cached_route__ but is not "
            "registered as any route's endpoint - @cached_route must sit inside "
            "@router.get, otherwise the router registers the undecorated function"
        )
