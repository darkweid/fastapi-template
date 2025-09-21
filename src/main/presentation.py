from fastapi import APIRouter, FastAPI

# Import routers here
from src.user import routers as user_routers
from src.system import routers as system_routers
from src.core.errors.exceptions import (
    AccessForbiddenException,
    CoreException,
    FilteringError,
    InstanceAlreadyExistsException,
    InstanceNotFoundException,
    InstanceProcessingException,
    NotAcceptableException,
    PermissionDeniedException,
    UnauthorizedException,
)
from src.core.errors.handlers import (
    AccessForbiddenExceptionHandler,
    CoreExceptionHandler,
    FilteringErrorHandler,
    InstanceAlreadyExistsExceptionHandler,
    InstanceNotFoundExceptionHandler,
    InstanceProcessingExceptionHandler,
    NotAcceptableExceptionHandler,
    PermissionDeniedExceptionHandler,
    UnauthorizedExceptionHandler,
    as_exception_handler,
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
    app.add_exception_handler(
        InstanceNotFoundException,
        as_exception_handler(InstanceNotFoundExceptionHandler()),
    )
    app.add_exception_handler(
        InstanceAlreadyExistsException,
        as_exception_handler(InstanceAlreadyExistsExceptionHandler()),
    )
    app.add_exception_handler(
        InstanceProcessingException,
        as_exception_handler(InstanceProcessingExceptionHandler()),
    )
    app.add_exception_handler(
        FilteringError, as_exception_handler(FilteringErrorHandler())
    )
    app.add_exception_handler(
        CoreException,
        as_exception_handler(CoreExceptionHandler()),
    )
    app.add_exception_handler(
        AccessForbiddenException,
        as_exception_handler(AccessForbiddenExceptionHandler()),
    )
    app.add_exception_handler(
        UnauthorizedException, as_exception_handler(UnauthorizedExceptionHandler())
    )
    app.add_exception_handler(
        NotAcceptableException, as_exception_handler(NotAcceptableExceptionHandler())
    )
    app.add_exception_handler(
        PermissionDeniedException,
        as_exception_handler(PermissionDeniedExceptionHandler()),
    )
