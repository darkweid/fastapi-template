from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
import sentry_sdk
from starlette.responses import Response

from loggers import get_logger
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

response_logger = get_logger("app.request.error_response", plain_format=True)

# Type for exception handler
HandlerCallable = Callable[[Request, Exception], Awaitable[Response]]


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


async def handle_infrastructure_exception(
    request: Request,
    exc: InfrastructureException,
) -> JSONResponse:
    error_type = "Infrastructure error"
    log_msg = format_log_message(request, error_type, exc.message, exc.additional_info)
    response_logger.error(log_msg)
    sentry_sdk.capture_exception(exc)
    return JSONResponse(
        status_code=500,
        content=format_error_response(error_type, exc.message),
    )


async def handle_request_validation_exception(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    error_type = "Request validation error"
    safe_detail = jsonable_encoder(exc.errors())
    log_msg = format_log_message(
        request,
        error_type,
        str(safe_detail),
        include_request_path=True,
    )
    response_logger.debug(log_msg)
    return JSONResponse(status_code=422, content={"detail": safe_detail})


async def handle_validation_error(
    request: Request,
    exc: ValidationError,
) -> JSONResponse:
    error_type = "Backend validation error"
    safe_detail = jsonable_encoder(exc.errors())
    log_msg = format_log_message(
        request,
        error_type,
        str(safe_detail),
        include_request_path=True,
    )
    response_logger.error(log_msg)
    sentry_sdk.capture_exception(exc)
    return JSONResponse(status_code=500, content={"detail": "Unexpected error"})


async def handle_core_exception(
    request: Request,
    exc: CoreException,
) -> JSONResponse:
    error_type = "Bad request"
    log_msg = format_log_message(request, error_type, exc.message, exc.additional_info)
    response_logger.info(log_msg)
    return JSONResponse(
        status_code=400,
        content=format_error_response(error_type, exc.message),
    )


async def handle_instance_not_found_exception(
    request: Request,
    exc: InstanceNotFoundException,
) -> JSONResponse:
    error_type = "Instance not found"
    log_msg = format_log_message(request, error_type, exc.message, exc.additional_info)
    response_logger.info(log_msg)
    return JSONResponse(
        status_code=404,
        content=format_error_response(error_type, exc.message),
    )


async def handle_instance_already_exists_exception(
    request: Request,
    exc: InstanceAlreadyExistsException,
) -> JSONResponse:
    error_type = "Instance already exists"
    log_msg = format_log_message(request, error_type, exc.message, exc.additional_info)
    response_logger.info(log_msg)
    return JSONResponse(
        status_code=409,
        content=format_error_response(error_type, exc.message),
    )


async def handle_instance_processing_exception(
    request: Request,
    exc: InstanceProcessingException,
) -> JSONResponse:
    error_type = "Instance processing error"
    log_msg = format_log_message(request, error_type, exc.message, exc.additional_info)
    response_logger.info(log_msg)
    return JSONResponse(
        status_code=400,
        content=format_error_response(error_type, exc.message),
    )


async def handle_payload_too_large_exception(
    request: Request,
    exc: PayloadTooLargeException,
) -> JSONResponse:
    error_type = "Payload too large"
    log_msg = format_log_message(request, error_type, exc.message, exc.additional_info)
    response_logger.info(log_msg)
    return JSONResponse(
        status_code=413,
        content=format_error_response(error_type, exc.message),
    )


async def handle_filtering_error(
    request: Request,
    exc: FilteringError,
) -> JSONResponse:
    error_type = "Filtering error"
    log_msg = format_log_message(request, error_type, exc.message, exc.additional_info)
    response_logger.warning(log_msg)
    return JSONResponse(
        status_code=400,
        content=format_error_response(error_type, exc.message),
    )


async def handle_unauthorized_exception(
    request: Request,
    exc: UnauthorizedException,
) -> JSONResponse:
    error_type = "Unauthorized"
    log_msg = format_log_message(request, error_type, exc.message, exc.additional_info)
    response_logger.warning(log_msg)
    return JSONResponse(
        status_code=401,
        content=format_error_response(error_type, exc.message),
    )


async def handle_access_forbidden_exception(
    request: Request,
    exc: AccessForbiddenException,
) -> JSONResponse:
    error_type = "Forbidden"
    log_msg = format_log_message(request, error_type, exc.message, exc.additional_info)
    response_logger.warning(log_msg)
    return JSONResponse(
        status_code=403,
        content=format_error_response(error_type, exc.message),
    )


async def handle_not_acceptable_exception(
    request: Request,
    exc: NotAcceptableException,
) -> JSONResponse:
    error_type = "Not Acceptable"
    log_msg = format_log_message(request, error_type, exc.message, exc.additional_info)
    response_logger.info(log_msg)
    return JSONResponse(
        status_code=406,
        content=format_error_response(error_type, exc.message),
    )


async def handle_permission_denied_exception(
    request: Request,
    exc: PermissionDeniedException,
) -> JSONResponse:
    error_type = "Permission Denied"
    log_msg = format_log_message(request, error_type, exc.message, exc.additional_info)
    response_logger.warning(log_msg)
    return JSONResponse(
        status_code=403,
        content=format_error_response(error_type, exc.message),
    )


async def handle_too_many_requests_exception(
    request: Request,
    exc: TooManyRequestsException,
) -> JSONResponse:
    error_type = "Too Many Requests"
    log_msg = format_log_message(request, error_type, exc.message)
    response_logger.info(log_msg)

    headers = {}
    if exc.retry_after:
        headers["Retry-After"] = str(exc.retry_after)

    return JSONResponse(
        status_code=429,
        content=format_error_response(error_type, exc.message),
        headers=headers,
    )
