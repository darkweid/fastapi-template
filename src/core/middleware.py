from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import re
import time
import traceback

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import sentry_sdk
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError
from starlette.responses import Response

from loggers import get_logger

logger = get_logger(__name__)
timing_logger = get_logger("src.request.timing", plain_format=True)
UNEXPECTED_ERROR_DETAIL = "Unexpected error"


@dataclass(slots=True)
class PostgresqlErrorHandlingResult:
    response: JSONResponse
    send_to_sentry: bool
    is_server_error: bool


def register_middlewares(app: FastAPI) -> None:
    """Registers all custom middlewares in proper order"""

    @app.middleware("http")
    async def security_headers_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
        return response

    @app.middleware("http")
    async def request_timing_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        start_time = time.perf_counter()
        response = await call_next(request)
        process_time = time.perf_counter() - start_time

        if process_time < 0.5:
            level = timing_logger.info
            category = "[FAST]"
        elif process_time < 2:
            level = timing_logger.warning
            category = "[MODERATE]"
        else:
            level = timing_logger.warning
            category = "[SLOW]"

        method = request.method
        path = request.url.path
        status_code = response.status_code
        duration = f"{process_time:.3f}s"

        level(f"{category} {method} {path} |{duration}|{status_code}")

        return response

    @app.middleware("http")
    async def database_error_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        try:
            return await call_next(request)
        except IntegrityError as exc:
            handled_result = handle_postgresql_error(exc)
            log_message = f"Integrity error at {request.url.path}: {str(exc.orig)}"
            if handled_result.is_server_error:
                logger.error(log_message, exc_info=True)
            else:
                logger.info(log_message)
            if handled_result.send_to_sentry:
                sentry_sdk.capture_exception(exc)
            return handled_result.response
        except OperationalError as e:
            logger.error(
                f"Database connection error at {request.url.path}: {str(e.orig)}"
            )
            sentry_sdk.capture_exception(e)
            return JSONResponse(
                status_code=500,
                content={
                    "detail": "Database connection error. Please try again later."
                },
            )

        except ProgrammingError as e:
            logger.error(f"SQL syntax error at {request.url.path}: {str(e.orig)}")
            sentry_sdk.capture_exception(e)
            return JSONResponse(
                status_code=500, content={"detail": "Database query error."}
            )

    @app.middleware("http")
    async def unexpected_error_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        try:
            return await call_next(request)
        except Exception as e:
            error_traceback = traceback.format_exc()
            logger.error(
                "Unexpected error at %s: %s\n%s",
                request.url.path,
                str(e),
                error_traceback,
            )
            sentry_sdk.capture_exception(e)
            return JSONResponse(
                status_code=500, content={"detail": UNEXPECTED_ERROR_DETAIL}
            )


def handle_postgresql_error(
    error: IntegrityError,
) -> PostgresqlErrorHandlingResult:
    """
    Build a structured handling result for PostgreSQL IntegrityError with HTTP response, Sentry flag, and log severity.
    """
    orig_error = error.orig
    sqlstate = getattr(orig_error, "sqlstate", None)
    detail_message = getattr(orig_error, "detail", None)

    raw_message = str(orig_error)

    if not detail_message:
        if "DETAIL:" in raw_message:
            detail_message = raw_message.split("DETAIL:")[-1].strip()
        else:
            detail_message = "No additional details provided."

    if sqlstate == "23505":  # UniqueViolation
        match = re.search(r"\(([^)]+)\)", detail_message)
        if match:
            first_value = match.group(1)
        else:
            first_value = detail_message
        return PostgresqlErrorHandlingResult(
            response=JSONResponse(status_code=409, content={"detail": first_value}),
            send_to_sentry=False,
            is_server_error=False,
        )
    if sqlstate == "23502":  # NotNullViolation
        column_name = getattr(orig_error, "column_name", None)
        column_match = (
            re.search(r'column "([^"]+)"', raw_message) if not column_name else None
        )
        missing_field = column_name or (column_match.group(1) if column_match else None)
        logger.error(
            "NotNullViolation on column=%s | detail=%s",
            missing_field,
            detail_message,
        )
        return PostgresqlErrorHandlingResult(
            response=JSONResponse(
                status_code=500, content={"detail": UNEXPECTED_ERROR_DETAIL}
            ),
            send_to_sentry=True,
            is_server_error=True,
        )
    if sqlstate == "23503":  # ForeignKeyViolation
        return PostgresqlErrorHandlingResult(
            response=JSONResponse(status_code=400, content={"detail": detail_message}),
            send_to_sentry=False,
            is_server_error=False,
        )
    if sqlstate == "23514":  # CheckViolation
        return PostgresqlErrorHandlingResult(
            response=JSONResponse(
                status_code=500, content={"detail": UNEXPECTED_ERROR_DETAIL}
            ),
            send_to_sentry=True,
            is_server_error=True,
        )
    if sqlstate == "23P01":  # ExclusionViolation
        return PostgresqlErrorHandlingResult(
            response=JSONResponse(
                status_code=500, content={"detail": UNEXPECTED_ERROR_DETAIL}
            ),
            send_to_sentry=True,
            is_server_error=True,
        )

    return PostgresqlErrorHandlingResult(
        response=JSONResponse(
            status_code=500, content={"detail": UNEXPECTED_ERROR_DETAIL}
        ),
        send_to_sentry=True,
        is_server_error=True,
    )
