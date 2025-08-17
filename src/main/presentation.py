from fastapi import APIRouter, FastAPI

# Import routers here
from src.healthcheck import routers as healthcheck_routers
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
)


def include_routers(app: FastAPI) -> None:
    v1_router = APIRouter()

    app.include_router(v1_router, prefix="/v1")
    app.include_router(healthcheck_routers.router, tags=["System"])


def include_exceptions_handlers(app: FastAPI) -> None:
    app.add_exception_handler(
        InstanceNotFoundException, InstanceNotFoundExceptionHandler()
    )
    app.add_exception_handler(
        InstanceAlreadyExistsException, InstanceAlreadyExistsExceptionHandler()
    )
    app.add_exception_handler(
        InstanceProcessingException, InstanceProcessingExceptionHandler()
    )
    app.add_exception_handler(FilteringError, FilteringErrorHandler())
    app.add_exception_handler(
        CoreException,
        CoreExceptionHandler(),
    )
    app.add_exception_handler(
        AccessForbiddenException, AccessForbiddenExceptionHandler()
    )
    app.add_exception_handler(UnauthorizedException, UnauthorizedExceptionHandler())
    app.add_exception_handler(NotAcceptableException, NotAcceptableExceptionHandler())
    app.add_exception_handler(
        PermissionDeniedException, PermissionDeniedExceptionHandler()
    )
