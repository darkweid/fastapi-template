from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
import sentry_sdk
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from loggers import get_logger
from src.core.errors.codes import ErrorCode
from src.core.errors.exceptions import CoreException

response_logger = get_logger("app.request.error_response", plain_format=True)

# Type for exception handler
HandlerCallable = Callable[[Request, Exception], Awaitable[Response]]

_HTTP_STATUS_TO_ERROR_CODE = {
    401: ErrorCode.UNAUTHORIZED,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.METHOD_NOT_ALLOWED,
    413: ErrorCode.PAYLOAD_TOO_LARGE,
    429: ErrorCode.RATE_LIMITED,
}


def format_error_response(code: ErrorCode, message: str | None) -> dict[str, Any]:
    """Build the flat error body every response follows: {"code", "message"}."""
    return {
        "code": code,
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


async def handle_core_exception(request: Request, exc: CoreException) -> JSONResponse:
    log_message = format_log_message(
        request, exc.error_code, exc.message, exc.additional_info
    )
    getattr(response_logger, exc.log_level)(log_message)
    if exc.capture_to_sentry:
        sentry_sdk.capture_exception(exc)
    headers = exc.response_headers()
    return JSONResponse(
        status_code=exc.status_code,
        content=format_error_response(exc.error_code, exc.message) | exc.body_extras(),
        headers=headers or None,
    )


async def handle_http_exception(
    request: Request,
    exc: StarletteHTTPException,
) -> JSONResponse:
    """Translate framework-raised HTTPExceptions onto the error contract.

    FastAPI's security utilities and Starlette's router raise bare
    HTTPExceptions (missing credentials -> 401, unmatched path -> 404,
    wrong method -> 405); without this handler they would answer
    Starlette's default {"detail": ...} body and break the contract.
    """
    code = _HTTP_STATUS_TO_ERROR_CODE.get(
        exc.status_code,
        (
            ErrorCode.INTERNAL_ERROR
            if exc.status_code >= 500
            else ErrorCode.PROCESSING_ERROR
        ),
    )
    message = exc.detail if isinstance(exc.detail, str) else None
    log_message = format_log_message(request, code, message, include_request_path=True)
    if exc.status_code >= 500:
        response_logger.error(log_message)
    else:
        response_logger.debug(log_message)
    return JSONResponse(
        status_code=exc.status_code,
        content=format_error_response(code, message),
        headers=exc.headers or None,
    )


def _format_validation_errors(errors: list[dict[str, Any]]) -> list[dict[str, str]]:
    formatted = []
    for error in errors:
        loc = tuple(error.get("loc", ()))
        # Only FastAPI's leading "body" prefix is dropped; any other segment
        # named "body" (e.g. a field literally called "body") is kept verbatim.
        if loc and loc[0] == "body":
            loc = loc[1:]
        location = [str(part) for part in loc]
        formatted.append(
            {
                "field": ".".join(location) or "body",
                "message": str(error.get("msg", "Invalid value")),
            }
        )
    return formatted


async def handle_request_validation_exception(
    request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    safe_errors = _format_validation_errors(jsonable_encoder(exc.errors()))
    log_message = format_log_message(
        request,
        ErrorCode.VALIDATION_ERROR,
        str(safe_errors),
        include_request_path=True,
    )
    response_logger.debug(log_message)
    return JSONResponse(
        status_code=422,
        content=format_error_response(
            ErrorCode.VALIDATION_ERROR, "Request validation failed"
        )
        | {"errors": safe_errors},
    )


async def handle_validation_error(
    request: Request,
    exc: ValidationError,
) -> JSONResponse:
    log_message = format_log_message(
        request,
        ErrorCode.INTERNAL_ERROR,
        str(jsonable_encoder(exc.errors())),
        include_request_path=True,
    )
    response_logger.error(log_message)
    sentry_sdk.capture_exception(exc)
    return JSONResponse(
        status_code=500,
        content=format_error_response(ErrorCode.INTERNAL_ERROR, "Unexpected error"),
    )
