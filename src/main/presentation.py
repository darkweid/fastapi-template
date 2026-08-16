from typing import cast

from fastapi import APIRouter, FastAPI
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from src.core.errors.exceptions import CoreException
from src.core.errors.handlers import (
    HandlerCallable,
    handle_core_exception,
    handle_request_validation_exception,
    handle_validation_error,
)
from src.system import routers as system_routers

# Import routers here
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
)


def include_routers(app: FastAPI) -> None:
    """
    Includes API routers into the FastAPI application.

    Parameters:
        app (FastAPI): The FastAPI application instance to which routers will
        be added.

    Returns:
        None
    """
    v1_router = APIRouter()
    v1_router.include_router(user_routers.router, prefix="/users", tags=["Users"])

    app.include_router(v1_router, prefix="/v1")
    app.include_router(system_routers.router, tags=["System"])


def include_exceptions_handlers(app: FastAPI) -> None:
    """
    Registers exception handlers for various custom exceptions with the provided FastAPI
    application instance.

    Parameters:
        app (FastAPI): The FastAPI application instance to which the exception handlers
        will be added.

    Returns:
        None
    """
    for exception_type, handler in EXCEPTION_HANDLERS:
        app.add_exception_handler(exception_type, handler)
