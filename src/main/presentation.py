from typing import cast

from fastapi import APIRouter, FastAPI
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError

from src.core.errors.exceptions import (
    AccessForbiddenException,
    CoreException,
    FilteringError,
    InfrastructureException,
    InstanceAlreadyExistsException,
    InstanceNotFoundException,
    InstanceProcessingException,
    NotAcceptableException,
    PayloadTooLargeException,
    PermissionDeniedException,
    TooManyRequestsException,
    UnauthorizedException,
)
from src.core.errors.handlers import (
    HandlerCallable,
    handle_access_forbidden_exception,
    handle_core_exception,
    handle_filtering_error,
    handle_infrastructure_exception,
    handle_instance_already_exists_exception,
    handle_instance_not_found_exception,
    handle_instance_processing_exception,
    handle_not_acceptable_exception,
    handle_payload_too_large_exception,
    handle_permission_denied_exception,
    handle_request_validation_exception,
    handle_too_many_requests_exception,
    handle_unauthorized_exception,
    handle_validation_error,
)
from src.system import routers as system_routers

# Import routers here
from src.user import routers as user_routers

EXCEPTION_HANDLERS: tuple[tuple[type[Exception], HandlerCallable], ...] = (
    (
        InfrastructureException,
        cast(HandlerCallable, handle_infrastructure_exception),
    ),
    (
        RequestValidationError,
        cast(HandlerCallable, handle_request_validation_exception),
    ),
    (
        ValidationError,
        cast(HandlerCallable, handle_validation_error),
    ),
    (
        InstanceNotFoundException,
        cast(HandlerCallable, handle_instance_not_found_exception),
    ),
    (
        InstanceAlreadyExistsException,
        cast(HandlerCallable, handle_instance_already_exists_exception),
    ),
    (
        InstanceProcessingException,
        cast(HandlerCallable, handle_instance_processing_exception),
    ),
    (
        PayloadTooLargeException,
        cast(HandlerCallable, handle_payload_too_large_exception),
    ),
    (
        FilteringError,
        cast(HandlerCallable, handle_filtering_error),
    ),
    (
        CoreException,
        cast(HandlerCallable, handle_core_exception),
    ),
    (
        AccessForbiddenException,
        cast(HandlerCallable, handle_access_forbidden_exception),
    ),
    (
        UnauthorizedException,
        cast(HandlerCallable, handle_unauthorized_exception),
    ),
    (
        NotAcceptableException,
        cast(HandlerCallable, handle_not_acceptable_exception),
    ),
    (
        PermissionDeniedException,
        cast(HandlerCallable, handle_permission_denied_exception),
    ),
    (
        TooManyRequestsException,
        cast(HandlerCallable, handle_too_many_requests_exception),
    ),
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
