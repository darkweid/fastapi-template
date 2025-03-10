import traceback
import sentry_sdk
from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError

from loggers import get_logger

logger = get_logger(__name__)


class ValidationErrorMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        try:
            response = await call_next(request)
            return response
        except ValidationError as e:
            logger.error("Validation error at %s: %s", request.url.path, e.errors())
            sentry_sdk.capture_exception(e)
            return JSONResponse(
                status_code=422,
                content={"detail": e.errors()}
            )


class DatabaseErrorMiddleware(BaseHTTPMiddleware):
    """Middleware for handling database errors"""

    async def dispatch(self, request: Request, call_next):
        try:
            response = await call_next(request)
            return response
        except IntegrityError as e:
            logger.error(f"Integrity error at {request.url.path}: {str(e.orig)}")
            sentry_sdk.capture_exception(e)
            return self.handle_postgresql_error(e)

        except OperationalError as e:
            logger.error(f"Database connection error at {request.url.path}: {str(e.orig)}")
            sentry_sdk.capture_exception(e)
            return JSONResponse(
                status_code=400,
                content={"detail": "Database connection error. Please try again later."}
            )

        except ProgrammingError as e:
            logger.error(f"SQL syntax error at {request.url.path}: {str(e.orig)}")
            sentry_sdk.capture_exception(e)
            return JSONResponse(
                status_code=400,
                content={"detail": "Database query error. Please check your request."}
            )

    def handle_postgresql_error(self, error):
        """PostgreSQL error handling (asyncpg)"""

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
            return self.response(409, detail_message)

        elif sqlstate == "23502":  # NotNullViolation
            return self.response(400, detail_message)

        elif sqlstate == "23503":  # ForeignKeyViolation
            return self.response(400, detail_message)

        elif sqlstate == "23514":  # CheckViolation
            return self.response(400, detail_message)

        elif sqlstate == "23P01":  # ExclusionViolation
            return self.response(400, detail_message)

        return self.response(400, detail_message)

    def response(self, status_code: int, message: str):
        """Creates a JSON response for the given status code and message"""
        return JSONResponse(status_code=status_code, content={"detail": message})


class UnexpectedErrorMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        try:
            return await call_next(request)
        except Exception as e:
            error_traceback = traceback.format_exc()  # getting full stacktrace
            logger.error(
                "Unexpected error at %s: %s\n%s",
                request.url.path,
                str(e),
                error_traceback,
            )

            sentry_sdk.capture_exception(e)
            return JSONResponse(
                status_code=400,
                content={
                    "detail": "Unexpected error",
                }
            )
