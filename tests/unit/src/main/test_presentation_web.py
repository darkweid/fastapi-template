from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sentry_sdk.integrations.asgi import SentryAsgiMiddleware

from src.core.errors.exceptions import UnauthorizedException
from src.main.presentation import include_exceptions_handlers, include_routers
from src.main.web import get_application


def test_include_routers_registers_expected_paths() -> None:
    app = FastAPI()
    include_routers(app)

    paths = {route.path for route in app.router.routes}

    assert "/v1/users/auth/login" in paths
    assert "/v1/users/auth/register" in paths
    assert "/health/" in paths


def test_include_exceptions_handlers_registers_handlers() -> None:
    app = FastAPI()
    include_exceptions_handlers(app)

    assert UnauthorizedException in app.exception_handlers


def test_get_application_registers_middlewares() -> None:
    app = get_application()

    middleware_classes = {middleware.cls for middleware in app.user_middleware}

    assert CORSMiddleware in middleware_classes
    assert SentryAsgiMiddleware in middleware_classes
    assert isinstance(app.openapi(), dict)
