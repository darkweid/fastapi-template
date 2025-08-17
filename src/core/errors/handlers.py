from typing import TypeVar

from fastapi import Request, FastAPI
from fastapi.responses import JSONResponse

from src.core.errors.exceptions import (
    CoreException,
    FilteringError,
    InstanceAlreadyExistsException,
    InstanceNotFoundException,
    InstanceProcessingException,
    UnauthorizedException,
    AccessForbiddenException,
    NotAcceptableException,
    PermissionDeniedException,
)

T = TypeVar("T", bound=CoreException)

# Error configuration mapping
ERROR_CONFIG: dict[type[CoreException], tuple[int, str]] = {
    # Base exception
    CoreException: (400, "Unknown server error"),
    # Specific exceptions
    InstanceNotFoundException: (404, "Instance not found"),
    InstanceAlreadyExistsException: (409, "Instance already exists"),
    InstanceProcessingException: (400, "Instance processing error"),
    FilteringError: (400, "Filtering error"),
    UnauthorizedException: (401, "Unauthorized"),
    AccessForbiddenException: (403, "Forbidden"),
    NotAcceptableException: (406, "Not Acceptable"),
    PermissionDeniedException: (423, "Permission Denied"),
}


def create_exception_handler(
    exception_class: type[T], status_code: int, error_message: str
) -> type:
    """
    Factory function to create exception handlers.

    Args:
        exception_class: The exception class this handler will handle
        status_code: HTTP status code to return
        error_message: Human-readable error message

    Returns:
        A new exception handler class
    """

    class ExceptionHandler:
        async def __call__(self, request: Request, exc: T) -> JSONResponse:
            return JSONResponse(
                status_code=status_code,
                content={"error": error_message, "detail": exc.message},
            )

    # Set a descriptive name for better debugging
    ExceptionHandler.__name__ = f"{exception_class.__name__}Handler"

    return ExceptionHandler


def generate_error_response(exc: CoreException) -> JSONResponse:
    """
    Generate a JSON response for an exception without needing a handler class.

    Args:
        exc: The exception to handle

    Returns:
        JSONResponse with appropriate status code and error details
    """
    exc_class = exc.__class__
    status_code, error_message = ERROR_CONFIG.get(
        exc_class, ERROR_CONFIG[CoreException]
    )

    return JSONResponse(
        status_code=status_code,
        content={"error": error_message, "detail": exc.message},
    )


def register_exception_handlers(app: FastAPI) -> None:
    """
    Register all exception handlers with a FastAPI app.

    Args:
        app: The FastAPI application instance
    """
    for exception_class, (status_code, error_message) in ERROR_CONFIG.items():
        handler_class = create_exception_handler(
            exception_class, status_code, error_message
        )
        app.add_exception_handler(exception_class, handler_class().__call__)


# For backward compatibility, keep the individual handler classes
CoreExceptionHandler = create_exception_handler(
    CoreException, *ERROR_CONFIG[CoreException]
)

InstanceNotFoundExceptionHandler = create_exception_handler(
    InstanceNotFoundException, *ERROR_CONFIG[InstanceNotFoundException]
)

InstanceAlreadyExistsExceptionHandler = create_exception_handler(
    InstanceAlreadyExistsException, *ERROR_CONFIG[InstanceAlreadyExistsException]
)

InstanceProcessingExceptionHandler = create_exception_handler(
    InstanceProcessingException, *ERROR_CONFIG[InstanceProcessingException]
)

FilteringErrorHandler = create_exception_handler(
    FilteringError, *ERROR_CONFIG[FilteringError]
)

UnauthorizedExceptionHandler = create_exception_handler(
    UnauthorizedException, *ERROR_CONFIG[UnauthorizedException]
)

AccessForbiddenExceptionHandler = create_exception_handler(
    AccessForbiddenException, *ERROR_CONFIG[AccessForbiddenException]
)

NotAcceptableExceptionHandler = create_exception_handler(
    NotAcceptableException, *ERROR_CONFIG[NotAcceptableException]
)

PermissionDeniedExceptionHandler = create_exception_handler(
    PermissionDeniedException, *ERROR_CONFIG[PermissionDeniedException]
)
