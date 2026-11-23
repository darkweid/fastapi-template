from collections.abc import Iterator
import re
from typing import Any

from aiohttp import ClientError as HttpClientError
from aiosmtplib import SMTPException, SMTPResponseException
from asyncpg import InterfaceError, PostgresError
from botocore.exceptions import ClientError as AwsClientError
from fastapi.exceptions import ResponseValidationError
from pydantic import AliasChoices, AliasPath, BaseModel, ValidationError
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

# Their text can carry a row, a bound value, an address, a URL or a Redis command;
# PendingRollbackError quotes the error that rolled its session back.
_ECHOING_ERRORS = (
    StatementError,
    PendingRollbackError,
    ValidationError,
    ResponseValidationError,
    SMTPException,
    RedisError,
    HttpClientError,
    PostgresError,
    InterfaceError,
)

# 08: connection exceptions, 08P01 among them: PgBouncer answers it for a failed
# server login and a full pool; 57P0x: the server is starting, stopping or ended
# the session; 53300: no connection slot left.
_OUTAGE_SQLSTATE_PREFIXES = ("08", "57P0", "53300")
# Closes the connection as well, but the database is gone for good.
_DATABASE_DROPPED = "57P04"

_SAFE_CODE = re.compile(r"[A-Za-z0-9._-]{1,64}")


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
    A wrong password, a missing or dropped database and a statement timeout do not.
    """
    if isinstance(error, DBAPIError) and sqlstate_of(error) == _DATABASE_DROPPED:
        return False
    if isinstance(error, (OperationalError, PoolTimeoutError)):
        return True
    if not isinstance(error, DBAPIError):
        return False
    return error.connection_invalidated or (sqlstate_of(error) or "").startswith(
        _OUTAGE_SQLSTATE_PREFIXES
    )


def _alias_names(alias: str | AliasPath | AliasChoices | None) -> Iterator[str]:
    if isinstance(alias, str):
        yield alias
    elif isinstance(alias, AliasPath):
        yield from (part for part in alias.path if isinstance(part, str))
    elif isinstance(alias, AliasChoices):
        for choice in alias.choices:
            yield from _alias_names(choice)


def _declared_names() -> set[str]:
    """Every field name and alias of every model: text of ours, whoever sent it."""
    names: set[str] = set()
    models: list[type[BaseModel]] = [BaseModel]
    visited: set[type[BaseModel]] = set()
    while models:
        model = models.pop()
        if model in visited:
            continue
        visited.add(model)
        models.extend(model.__subclasses__())
        for name, field in model.model_fields.items():
            names.add(name)
            # An `alias` is copied here by pydantic.
            names.update(_alias_names(field.validation_alias))
    return names


def describe_validation_error(exc: ValidationError) -> str:
    """
    Summarize a backend ValidationError by model, location and error type.

    `str(exc)` prints every `input_value`, `errors()` carries `input` and a
    `ctx` that can repeat it, and a validator's own message may quote it, so
    none of them reach a log line or Sentry. A location can echo input as well
    (a dict key, a forbidden extra field), so every part of it that is not a
    declared field name is printed as `*`.
    """
    declared = _declared_names()
    locations = "; ".join(
        f"{'.'.join(part if part in declared else '*' for part in map(str, error['loc'])) or '<root>'}: {error['type']}"
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
    if isinstance(error, SMTPResponseException) and isinstance(error.code, int):
        return f"{type(error).__name__} code={int(error.code)}"
    if isinstance(error, AwsClientError):
        code = error.response.get("Error", {}).get("Code")
        if isinstance(code, str) and _SAFE_CODE.fullmatch(code):
            return f"{type(error).__name__} code={code}"
    if isinstance(error, CoreException) and error.message:
        return f"{type(error).__name__}: {error.message}"
    return type(error).__name__


def scrub(error: BaseException) -> ScrubbedError:
    """Stand-in to log, report, or raise `from None` in place of `error`."""
    return scrubbed(describe_error(error), error)


def _printed_with(error: BaseException) -> Iterator[BaseException]:
    """`error` and whatever a traceback of it prints: causes, contexts, group members.

    A loop and not a recursion: a retry loop can chain more errors than the
    interpreter allows frames.
    """
    seen: set[int] = set()
    pending: list[BaseException | None] = [error]
    while pending:
        link = pending.pop()
        if link is None or id(link) in seen:
            continue
        seen.add(id(link))
        yield link
        if isinstance(link, BaseExceptionGroup):
            pending.extend(reversed(link.exceptions))
        if not link.__suppress_context__:
            pending.append(link.__context__)
        pending.append(link.__cause__)


def reportable(
    error: BaseException, also_echoing: tuple[type[BaseException], ...] = ()
) -> BaseException:
    """
    `error` itself, or a scrubbed stand-in when its traceback would echo input.

    For code that meets any exception and must keep the text of an ordinary one.
    `also_echoing` names classes whose text quotes input in the caller's own work.
    """
    for link in _printed_with(error):
        if isinstance(link, (*_ECHOING_ERRORS, *also_echoing)):
            summary = describe_error(error)
            if link is not error:
                summary += f" (from {describe_error(link)})"
            return scrubbed(summary, error)
    return error
