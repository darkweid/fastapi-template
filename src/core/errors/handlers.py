from typing import Any, TypeVar, cast
from collections.abc import Callable, Awaitable

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.responses import Response

from src.core.errors.exceptions import (
    CoreException,
    FilteringError,
    InstanceAlreadyExistsException,
    InstanceNotFoundException,
    InstanceProcessingException,
)
from loggers import get_logger

response_logger = get_logger("app.request.4xxresponse", plain_format=True)

# Type for exception handler
ExcType = TypeVar("ExcType", bound=Exception)
HandlerCallable = Callable[[Request, Exception], Awaitable[Response]]


def as_exception_handler(handler: Any) -> HandlerCallable:
    """
    Convert a handler class instance to a compatible exception handler callable.
    This helps mypy understand the correct typing for FastAPI exception handlers.

    Args:
        handler: An instance of an exception handler class with __call__ method

    Returns:
        A callable with the correct type signature for FastAPI exception handlers
    """
    return cast(HandlerCallable, handler.__call__)


def format_error_response(error_type: str, message: str | None) -> dict[str, Any]:
    """
    Format error response content for JSONResponse

    Args:
        error_type: Type of error (e.g., "Unauthorized", "Instance not found")
        message: Detailed error message

    Returns:
        Dictionary with error information
    """
    return {
        "error": error_type,
        "message": message or "No additional details available",
    }


def format_log_message(
    request: Request,
    error_type: str,
    message: str | None,
    additional_info: dict[str, Any] | None = None,
    include_request_path: bool = False,
) -> str:
    """
    Format error message for logging

    Args:
        request: FastAPI Request object
        error_type: Type of error
        message: Error message
        additional_info: Additional context information for logs only (not shown to clients)
        include_request_path: Include request path and method in the log message

    Returns:
        Formatted log message
    """
    # Normalize message text and length
    raw_msg = message or "No additional details available"
    msg = " ".join(raw_msg.split())
    if len(msg) > 500:
        msg = msg[:497] + "..."

    # Safely capitalize an error type
    et = (error_type or "").strip()
    err = (et[:1].upper() + et[1:]) if et else "Error"

    request_id = request.headers.get("x-request-id") or getattr(
        getattr(request, "state", object()), "request_id", None
    )

    prefix = f"[{request_id}] " if request_id else ""
    log_msg = f"{prefix}[{err}] {msg}"

    if include_request_path:
        endpoint = request.url.path
        method = request.method
        log_msg = f"{prefix}[{err}] {method} {endpoint} | {msg}"

    if additional_info:
        sensitive = {
            "authorization",
            "token",
            "password",
            "secret",
            "api_key",
            "api-key",
        }

        def mask(k: str, v: Any) -> str:
            return "***" if k.lower() in sensitive else repr(v)

        additional_str = ", ".join(
            f"{k}={mask(k, additional_info[k])}" for k in sorted(additional_info)
        )
        log_msg = f"{log_msg} | Additional info: {additional_str}"

    return log_msg


class CoreExceptionHandler:
    async def __call__(self, request: Request, exc: CoreException) -> JSONResponse:
        error_type = "Bad request"
        log_msg = format_log_message(
            request, error_type, exc.message, exc.additional_info
        )
        response_logger.info(log_msg)
        return JSONResponse(
            status_code=400,
            content=format_error_response(error_type, exc.message),
        )


class InstanceNotFoundExceptionHandler:
    async def __call__(
        self, request: Request, exc: InstanceNotFoundException
    ) -> JSONResponse:
        error_type = "Instance not found"
        log_msg = format_log_message(
            request, error_type, exc.message, exc.additional_info
        )
        response_logger.info(log_msg)
        return JSONResponse(
            status_code=404,
            content=format_error_response(error_type, exc.message),
        )


class InstanceAlreadyExistsExceptionHandler:
    async def __call__(
        self, request: Request, exc: InstanceAlreadyExistsException
    ) -> JSONResponse:
        error_type = "Instance already exists"
        log_msg = format_log_message(
            request, error_type, exc.message, exc.additional_info
        )
        response_logger.info(log_msg)
        return JSONResponse(
            status_code=409,
            content=format_error_response(error_type, exc.message),
        )


class InstanceProcessingExceptionHandler:
    async def __call__(
        self, request: Request, exc: InstanceProcessingException
    ) -> JSONResponse:
        error_type = "Instance processing error"
        log_msg = format_log_message(
            request, error_type, exc.message, exc.additional_info
        )
        response_logger.info(log_msg)
        return JSONResponse(
            status_code=400,
            content=format_error_response(error_type, exc.message),
        )


class FilteringErrorHandler:
    async def __call__(self, request: Request, exc: FilteringError) -> JSONResponse:
        error_type = "Filtering error"
        log_msg = format_log_message(
            request, error_type, exc.message, exc.additional_info
        )
        response_logger.warning(log_msg)
        return JSONResponse(
            status_code=400,
            content=format_error_response(error_type, exc.message),
        )


class UnauthorizedExceptionHandler:
    async def __call__(self, request: Request, exc: CoreException) -> JSONResponse:
        error_type = "Unauthorized"
        log_msg = format_log_message(
            request, error_type, exc.message, exc.additional_info
        )
        response_logger.warning(log_msg)
        return JSONResponse(
            status_code=401,
            content=format_error_response(error_type, exc.message),
        )


class AccessForbiddenExceptionHandler:
    async def __call__(self, request: Request, exc: CoreException) -> JSONResponse:
        error_type = "Forbidden"
        log_msg = format_log_message(
            request, error_type, exc.message, exc.additional_info
        )
        response_logger.warning(log_msg)
        return JSONResponse(
            status_code=403,
            content=format_error_response(error_type, exc.message),
        )


class NotAcceptableExceptionHandler:
    async def __call__(self, request: Request, exc: CoreException) -> JSONResponse:
        error_type = "Not Acceptable"
        log_msg = format_log_message(
            request, error_type, exc.message, exc.additional_info
        )
        response_logger.info(log_msg)
        return JSONResponse(
            status_code=406,
            content=format_error_response(error_type, exc.message),
        )


class PermissionDeniedExceptionHandler:
    async def __call__(self, request: Request, exc: CoreException) -> JSONResponse:
        error_type = "Permission Denied"
        log_msg = format_log_message(
            request, error_type, exc.message, exc.additional_info
        )
        response_logger.warning(log_msg)
        return JSONResponse(
            status_code=423,
            content=format_error_response(error_type, exc.message),
        )
