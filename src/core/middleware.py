from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import sentry_sdk
from sqlalchemy.exc import (
    DBAPIError,
    IntegrityError,
    TimeoutError as PoolTimeoutError,
)
from starlette.responses import Response

from loggers import get_logger
from src.core.errors.codes import ErrorCode
from src.core.errors.describe import (
    describe_database_error,
    describe_integrity_error,
    is_database_outage,
    reportable,
    sqlstate_of,
)
from src.core.errors.handlers import format_error_response
from src.core.errors.scrubbed import scrubbed
from src.core.request_context import request_id_var, resolve_request_id

logger = get_logger(__name__)
timing_logger = get_logger("src.request.timing", plain_format=True)
UNEXPECTED_ERROR_DETAIL = "Unexpected error"
STRICT_CONTENT_SECURITY_POLICY = "default-src 'self'; frame-ancestors 'none'"
DOCS_CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "base-uri 'self'; "
    "object-src 'none'; "
    "frame-ancestors 'none'; "
    "connect-src 'self'; "
    "img-src 'self' data: https://fastapi.tiangolo.com; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "font-src 'self' data: https://cdn.jsdelivr.net"
)
BASE_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains; preload",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}
DOCS_PATHS = frozenset({"/openapi.json", "/redoc"})


def _is_docs_route(path: str) -> bool:
    return path in DOCS_PATHS or path == "/docs" or path.startswith("/docs/")


def _get_content_security_policy(path: str) -> str:
    if _is_docs_route(path):
        return DOCS_CONTENT_SECURITY_POLICY
    return STRICT_CONTENT_SECURITY_POLICY


@dataclass(slots=True)
class PostgresqlErrorHandlingResult:
    response: JSONResponse
    send_to_sentry: bool
    is_server_error: bool


def _internal_error_response() -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content=format_error_response(
            ErrorCode.INTERNAL_ERROR, UNEXPECTED_ERROR_DETAIL
        ),
    )


def register_middlewares(app: FastAPI) -> None:
    """Registers all custom middlewares in proper order"""

    @app.middleware("http")
    async def request_timing_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        start_time = time.perf_counter()
        response = await call_next(request)
        process_time = time.perf_counter() - start_time

        if process_time < 0.5:
            category, level = "[FAST]", timing_logger.info
        else:
            category = "[MODERATE]" if process_time < 2 else "[SLOW]"
            level = timing_logger.warning

        level(
            f"{category} {request.method} {request.url.path} "
            f"|{process_time:.3f}s|{response.status_code}"
        )

        return response

    @app.middleware("http")
    async def database_error_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        try:
            return await call_next(request)
        except IntegrityError as exc:
            handled_result = handle_postgresql_error(exc)
            summary = describe_integrity_error(exc)
            log_message = f"Integrity error at {request.url.path}: {summary}"
            if handled_result.is_server_error:
                logger.error(log_message, exc_info=scrubbed(summary, exc))
            else:
                logger.info(log_message)
            if handled_result.send_to_sentry:
                sentry_sdk.capture_exception(scrubbed(summary, exc))
            return handled_result.response
        except (DBAPIError, PoolTimeoutError) as exc:
            summary = describe_database_error(exc)
            failure = scrubbed(summary, exc)
            sentry_sdk.capture_exception(failure)
            if is_database_outage(exc):
                logger.error(f"Database unavailable at {request.url.path}: {summary}")
                return JSONResponse(
                    status_code=503,
                    content=format_error_response(
                        ErrorCode.SERVICE_UNAVAILABLE,
                        "Database is temporarily unavailable. Please try again later.",
                    ),
                )
            logger.error(
                f"Database error at {request.url.path}: {summary}", exc_info=failure
            )
            return _internal_error_response()

    @app.middleware("http")
    async def unexpected_error_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        try:
            return await call_next(request)
        except Exception as e:
            failure = reportable(e)
            logger.error(
                "Unexpected error at %s: %s",
                request.url.path,
                failure,
                exc_info=failure,
            )
            sentry_sdk.capture_exception(failure)
            return _internal_error_response()

    @app.middleware("http")
    async def security_headers_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Outside the error middlewares: their own 4xx/5xx responses carry the
        # headers too.
        response = await call_next(request)
        for name, value in BASE_SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        response.headers.setdefault(
            "Content-Security-Policy",
            _get_content_security_policy(request.url.path),
        )
        return response

    @app.middleware("http")
    async def request_id_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        # Registered last so it wraps every other custom middleware here
        # (Starlette runs the most-recently-registered middleware outermost):
        # the id is set before request_timing_middleware runs and only reset
        # after it has logged, and the header lands on responses produced by
        # database_error_middleware/unexpected_error_middleware too.
        resolved_id = resolve_request_id(request.headers.get("x-request-id"))
        token = request_id_var.set(resolved_id)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-ID"] = resolved_id
        return response


def handle_postgresql_error(
    error: IntegrityError,
) -> PostgresqlErrorHandlingResult:
    """
    Map a PostgreSQL IntegrityError onto a response, a Sentry flag and a log severity.

    A unique violation answers 409, which on registration tells a caller that
    an address is already taken. That is a deliberate trade: registration is the
    one route that tells, and a client that cannot distinguish "already
    registered" from a generic failure sends the user in circles. Login, resend
    and password reset answer the same either way. The conflicting value itself
    is never echoed back — the DB DETAIL can carry someone else's data (an
    email, a phone number), so only the status and code are client-facing.

    Everything else (check and exclusion violations, NOT NULL, unknown states)
    is a bug in the application rather than bad input: it answers 500 and goes
    to Sentry.
    """
    sqlstate = sqlstate_of(error)

    if sqlstate == "23505":  # UniqueViolation
        return PostgresqlErrorHandlingResult(
            response=JSONResponse(
                status_code=409,
                content=format_error_response(
                    ErrorCode.ALREADY_EXISTS, "Resource already exists."
                ),
            ),
            send_to_sentry=False,
            is_server_error=False,
        )
    if sqlstate == "23503":  # ForeignKeyViolation
        return PostgresqlErrorHandlingResult(
            response=JSONResponse(
                status_code=400,
                content=format_error_response(
                    ErrorCode.INVALID_REFERENCE,
                    "Referenced resource does not exist.",
                ),
            ),
            send_to_sentry=False,
            is_server_error=False,
        )
    return PostgresqlErrorHandlingResult(
        response=_internal_error_response(),
        send_to_sentry=True,
        is_server_error=True,
    )
