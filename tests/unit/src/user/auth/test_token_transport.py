from unittest.mock import AsyncMock

from fastapi import Depends, FastAPI, routing
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
import pytest

from src.core.schemas import TokenModel
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
from tests.factories.token_factory import build_refresh_payload
from tests.factories.user_factory import build_user
from tests.helpers.limiter import noop_rate_limiter
from tests.helpers.overrides import DependencyOverrides
from tests.helpers.providers import ProvideValue


class FakeUseCase:
    def __init__(self, result) -> None:
        self.execute = AsyncMock(return_value=result)


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
        transport: TokenTransport = Depends(get_token_transport),
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
    assert response.json() == {"access_token": "a", "refresh_token": None}

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

    assert response.json() == {"access_token": "a", "refresh_token": "r"}
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
async def test_refresh_via_header_needs_no_csrf(
    async_client,
    dependency_overrides: DependencyOverrides,
) -> None:
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
    assert response.json() == {"access_token": "a2", "refresh_token": "r2"}
    assert response.headers.get_list("set-cookie") == []


def _declares_transport(dependant) -> bool:
    if dependant.call is get_token_transport:
        return True
    return any(_declares_transport(sub) for sub in dependant.dependencies)


def test_refresh_cookie_path_matches_the_mounted_route(app) -> None:
    # `app.routes` nests included routers behind `_IncludedRouter` wrappers, so the
    # mounted path (with prefix applied) is only visible through the effective route
    # contexts FastAPI itself walks to build the OpenAPI schema.
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
