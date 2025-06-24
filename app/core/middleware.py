import traceback

import sentry_sdk
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError

from loggers import get_logger

logger = get_logger(__name__)


def register_middlewares(app: FastAPI) -> None:
    """Registers all custom middlewares in proper order"""

    @app.middleware("http")
    async def validation_error_middleware(request: Request, call_next):
        try:
            return await call_next(request)
        except ValidationError as e:
            logger.error("Validation error at %s: %s", request.url.path, e.errors())
            sentry_sdk.capture_exception(e)
            return JSONResponse(
                status_code=422,
                content={"detail": jsonable_encoder(e.errors())}
            )

    @app.middleware("http")
    async def database_error_middleware(request: Request, call_next):
        try:
            return await call_next(request)
        except IntegrityError as e:
            logger.error(f"Integrity error at {request.url.path}: {str(e.orig)}")
            sentry_sdk.capture_exception(e)
            return handle_postgresql_error(e)

        except OperationalError as e:
            logger.error(f"Database connection error at {request.url.path}: {str(e.orig)}")
            sentry_sdk.capture_exception(e)
            return JSONResponse(
                status_code=500,
                content={"detail": "Database connection error. Please try again later."}
            )

        except ProgrammingError as e:
            logger.error(f"SQL syntax error at {request.url.path}: {str(e.orig)}")
            sentry_sdk.capture_exception(e)
            return JSONResponse(
                status_code=500,
                content={"detail": "Database query error."}
            )

    @app.middleware("http")
    async def unexpected_error_middleware(request: Request, call_next):
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
                status_code=500,
                content={"detail": "Unexpected error"}
            )


def handle_postgresql_error(error: IntegrityError) -> JSONResponse:
    """PostgreSQL error handling (asyncpg-style)"""
    orig_error = error.orig
    sqlstate = getattr(orig_error, "sqlstate", None)
    detail_message = getattr(orig_error, "detail", None)

    if not detail_message:
        error_message = str(orig_error)
        if "DETAIL:" in error_message:
            detail_message = error_message.split("DETAIL:")[-1].strip()
        else:
            detail_message = "No additional details provided."

    if sqlstate == "23505":  # UniqueViolation
        return JSONResponse(status_code=409, content={"detail": detail_message})
    elif sqlstate == "23502":  # NotNullViolation
        return JSONResponse(status_code=422, content={"detail": detail_message})
    elif sqlstate == "23503":  # ForeignKeyViolation
        return JSONResponse(status_code=400, content={"detail": detail_message})
    elif sqlstate == "23514":  # CheckViolation
        return JSONResponse(status_code=400, content={"detail": detail_message})
    elif sqlstate == "23P01":  # ExclusionViolation
        return JSONResponse(status_code=400, content={"detail": detail_message})

    return JSONResponse(status_code=400, content={"detail": detail_message})
