from collections.abc import Iterator
from typing import Any

from aiosmtplib import SMTPException
from fastapi.exceptions import ResponseValidationError
from pydantic import ValidationError
from redis.exceptions import RedisError
from sqlalchemy.exc import (
    DBAPIError,
    IntegrityError,
    OperationalError,
    PendingRollbackError,
    StatementError,
    TimeoutError as PoolTimeoutError,
)

from src.core.errors.exceptions import CoreException
from src.core.errors.scrubbed import ScrubbedError, scrubbed

_DIAGNOSTICS = ("constraint_name", "table_name", "column_name")

# Their text can carry a row, a bound value, an address or a Redis command;
# PendingRollbackError quotes the error that rolled its session back.
_ECHOING_ERRORS = (
    StatementError,
    PendingRollbackError,
    ValidationError,
    ResponseValidationError,
    SMTPException,
    RedisError,
)

# 08: connection exceptions; 57P0x: the server is starting, stopping or dropped
# the session; 53300: no connection slot left.
_OUTAGE_SQLSTATE_PREFIXES = ("08", "57P0", "53300")


def _diagnostic(error: StatementError, name: str) -> str | None:
    """
    Read one diagnostic field wherever the driver put it.

    SQLAlchemy's asyncpg adapter copies only `sqlstate` onto the DBAPI error
    and keeps the asyncpg exception, which has the rest, as its `__cause__`;
    psycopg exposes them under `.diag`.
    """
    driver_error = error.orig
    for source in (
        driver_error,
        getattr(driver_error, "__cause__", None),
        getattr(driver_error, "diag", None),
    ):
        value: Any = getattr(source, name, None)
        if isinstance(value, str) and value:
            return value
    return None


def sqlstate_of(error: StatementError) -> str | None:
    return _diagnostic(error, "sqlstate")


def _schema_objects(error: StatementError) -> list[str]:
    return [
        f"{name}={value}"
        for name in _DIAGNOSTICS
        if (value := _diagnostic(error, name)) is not None
    ]


def describe_integrity_error(error: IntegrityError) -> str:
    """
    Name the violation by its SQLSTATE and schema objects, never by its values.

    The driver's message carries the DETAIL line (`Key (email)=(...) already
    exists`, `Failing row contains (...)`), which echoes whatever a caller
    sent, so neither it nor `str(error)` goes to a log or to Sentry.
    """
    return " ".join([f"sqlstate={sqlstate_of(error)}", *_schema_objects(error)])


def describe_database_error(error: StatementError | PoolTimeoutError) -> str:
    """
    Name a failed statement by its classes, SQLSTATE and schema objects.

    `hide_parameters` drops the parameter list, not the driver's own text,
    which quotes the bound value as well (`invalid input for query argument
    $1: ...`), so `str(error)` never goes to a log or to Sentry.
    """
    if isinstance(error, PoolTimeoutError):
        return "PoolTimeoutError"
    parts = [type(error).__name__]
    if error.orig is not None:
        driver_error = getattr(error.orig, "__cause__", None) or error.orig
        parts.append(f"driver={type(driver_error).__name__}")
    if sqlstate := sqlstate_of(error):
        parts.append(f"sqlstate={sqlstate}")
    return " ".join([*parts, *_schema_objects(error)])


def is_database_outage(error: StatementError | PoolTimeoutError) -> bool:
    """
    Whether the database could not be reached, as opposed to refusing the work.

    SQLAlchemy's asyncpg dialect raises no OperationalError of its own: the
    engines' `do_connect` listener makes one for a connection that could not be
    opened, and a connection lost later is known by its SQLSTATE or by
    `connection_invalidated`. A session the server ended for idling in a
    transaction counts too, since the driver reports it as a closed connection.
    A wrong password, a missing database and a statement timeout do not.
    """
    if isinstance(error, (OperationalError, PoolTimeoutError)):
        return True
    if not isinstance(error, DBAPIError):
        return False
    return error.connection_invalidated or (sqlstate_of(error) or "").startswith(
        _OUTAGE_SQLSTATE_PREFIXES
    )


def describe_validation_error(exc: ValidationError) -> str:
    """
    Summarize a backend ValidationError by model, location and error type.

    `str(exc)` prints every `input_value`, `errors()` carries `input` and a
    `ctx` that can repeat it, and a validator's own message may quote it, so
    none of them reach a log line or Sentry.
    """
    locations = "; ".join(
        f"{'.'.join(str(part) for part in error['loc']) or '<root>'}: {error['type']}"
        for error in exc.errors(
            include_url=False, include_context=False, include_input=False
        )
    )
    return f"{exc.title}: {locations}"


def describe_error(error: BaseException) -> str:
    """What may be logged about any exception: its class, plus the safe fields
    of the ones that have some. A project exception's message is our own text."""
    if isinstance(error, (StatementError, PoolTimeoutError)):
        return describe_database_error(error)
    if isinstance(error, ValidationError):
        return describe_validation_error(error)
    if isinstance(error, CoreException) and error.message:
        return f"{type(error).__name__}: {error.message}"
    return type(error).__name__


def scrub(error: BaseException) -> ScrubbedError:
    """Stand-in to log, report, or raise `from None` in place of `error`."""
    return scrubbed(describe_error(error), error)


def _printed_with(error: BaseException, seen: set[int]) -> Iterator[BaseException]:
    """`error` and whatever a traceback of it prints: causes, contexts, group members."""
    if id(error) in seen:
        return
    seen.add(id(error))
    yield error
    linked = [error.__cause__]
    if not error.__suppress_context__:
        linked.append(error.__context__)
    if isinstance(error, BaseExceptionGroup):
        linked.extend(error.exceptions)
    for link in linked:
        if link is not None:
            yield from _printed_with(link, seen)


def reportable(error: BaseException) -> BaseException:
    """
    `error` itself, or a scrubbed stand-in when its traceback would echo input.

    For code that meets any exception and must keep the text of an ordinary one.
    """
    for link in _printed_with(error, set()):
        if isinstance(link, _ECHOING_ERRORS):
            summary = describe_error(error)
            if link is not error:
                summary += f" (from {describe_error(link)})"
            return scrubbed(summary, error)
    return error
