from typing import cast

from fastapi import APIRouter, FastAPI
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.core.errors.exceptions import CoreException
from src.core.errors.handlers import (
    HandlerCallable,
    handle_core_exception,
    handle_http_exception,
    handle_request_validation_exception,
    handle_validation_error,
)
from src.note import routers as note_routers
from src.system import routers as system_routers
from src.user import routers as user_routers

EXCEPTION_HANDLERS: tuple[tuple[type[Exception], HandlerCallable], ...] = (
    # Starlette resolves handlers over the exception's MRO, so this one entry
    # covers every CoreException subclass, current and future.
    (CoreException, cast(HandlerCallable, handle_core_exception)),
    (
        RequestValidationError,
        cast(HandlerCallable, handle_request_validation_exception),
    ),
    (ValidationError, cast(HandlerCallable, handle_validation_error)),
    # FastAPI's HTTPException subclasses Starlette's, so this one registration
    # covers framework-raised exceptions from both (missing credentials,
    # unmatched routes, wrong methods) that would otherwise bypass the contract.
    (StarletteHTTPException, cast(HandlerCallable, handle_http_exception)),
)


def include_routers(app: FastAPI) -> None:
    """Mount every domain router under /v1. A new router is added here."""
    v1_router = APIRouter()
    v1_router.include_router(user_routers.router, prefix="/users", tags=["Users"])
    v1_router.include_router(note_routers.router, prefix="/notes", tags=["Notes"])

    app.include_router(v1_router, prefix="/v1")
    app.include_router(system_routers.router, tags=["System"])


def include_exceptions_handlers(app: FastAPI) -> None:
    """Register the exception handlers.

    A new project exception needs no entry here: the generic handler serializes
    whatever `status_code`, `error_code` and `log_level` the class declares.
    Only errors raised outside that hierarchy need one of their own.
    """
    for exception_type, handler in EXCEPTION_HANDLERS:
        app.add_exception_handler(exception_type, handler)
