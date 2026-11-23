import traceback

from aiosmtplib import SMTPRecipientRefused, SMTPRecipientsRefused
from fastapi.exceptions import ResponseValidationError
from pydantic import BaseModel, ValidationError
import pytest
from redis.exceptions import ResponseError
from sqlalchemy.exc import (
    DBAPIError,
    InterfaceError,
    OperationalError,
    PendingRollbackError,
    StatementError,
    TimeoutError as PoolTimeoutError,
)

from src.core.errors.describe import (
    describe_database_error,
    describe_error,
    is_database_outage,
    reportable,
    scrub,
)
from src.core.errors.exceptions import InfrastructureException
from src.core.errors.scrubbed import ScrubbedError

SECRET = "secret@example.com"


class DriverError(Exception):
    def __init__(self, sqlstate: str | None = None) -> None:
        super().__init__(f"invalid input for query argument $1: '{SECRET}'")
        self.sqlstate = sqlstate


class AsyncpgDataError(Exception):
    pass


def database_error(sqlstate: str | None = None, **options: bool) -> DBAPIError:
    return DBAPIError("SELECT 1", None, DriverError(sqlstate), **options)


def validation_error() -> ValidationError:
    class Payload(BaseModel):
        age: int

    with pytest.raises(ValidationError) as caught:
        Payload.model_validate({"age": SECRET})
    return caught.value


ECHOING = [
    database_error(),
    StatementError(f"bad value {SECRET}", "SELECT 1", None, None),
    PendingRollbackError(f"Original exception was: {SECRET}"),
    validation_error(),
    ResponseValidationError([{"loc": ("response",), "input": SECRET}]),
    SMTPRecipientsRefused([SMTPRecipientRefused(550, "no such user", SECRET)]),
    ResponseError(f"unknown command 'SET {SECRET}'"),
]


@pytest.mark.parametrize("sqlstate", ["08001", "08006", "57P01", "57P03", "53300"])
def test_an_unreachable_database_is_an_outage(sqlstate: str) -> None:
    assert is_database_outage(database_error(sqlstate)) is True


def test_an_outage_without_an_sqlstate() -> None:
    assert is_database_outage(database_error(connection_invalidated=True)) is True
    assert is_database_outage(OperationalError("c", None, Exception())) is True
    assert is_database_outage(PoolTimeoutError("QueuePool limit")) is True


@pytest.mark.parametrize("sqlstate", ["28P01", "3D000", "57014", "53200", None])
def test_a_refusal_is_not_an_outage(sqlstate: str | None) -> None:
    """A wrong password is a misconfiguration and a statement timeout a slow
    query: answering 503 to either hides it behind a retry."""
    assert is_database_outage(database_error(sqlstate)) is False


def test_an_interface_error_is_not_an_outage() -> None:
    """asyncpg's DataError derives from InterfaceError, so the class says nothing."""
    assert is_database_outage(InterfaceError("q", None, DriverError())) is False


def test_a_statement_that_never_reached_the_driver_is_not_an_outage() -> None:
    assert is_database_outage(StatementError("bad", "SELECT 1", None, None)) is False


def test_a_database_error_is_named_by_its_classes_and_state() -> None:
    error = database_error("22000")
    assert error.orig is not None
    error.orig.__cause__ = AsyncpgDataError(SECRET)

    assert describe_database_error(error) == (
        "DBAPIError driver=AsyncpgDataError sqlstate=22000"
    )


@pytest.mark.parametrize("error", ECHOING, ids=lambda error: type(error).__name__)
def test_no_description_or_stand_in_quotes_the_input(error: Exception) -> None:
    assert SECRET in str(error)
    assert SECRET not in describe_error(error)

    for stand_in in (scrub(error), reportable(error)):
        assert isinstance(stand_in, ScrubbedError)
        assert SECRET not in "".join(traceback.format_exception(stand_in))


def test_a_project_exception_keeps_its_own_message() -> None:
    assert describe_error(InfrastructureException("S3 is disabled")) == (
        "InfrastructureException: S3 is disabled"
    )


def test_an_ordinary_error_is_reportable_as_itself() -> None:
    error = RuntimeError("boom")

    assert reportable(error) is error


def wrapping(cause: BaseException | None, context: bool = False) -> RuntimeError:
    error = RuntimeError("wrapped")
    error.__context__ = database_error() if context else None
    if cause is not None or not context:
        error.__cause__ = cause
    return error


@pytest.mark.parametrize(
    "error",
    [
        wrapping(database_error()),
        wrapping(ExceptionGroup("several", [database_error()])),
        wrapping(None, context=True),
    ],
    ids=["cause", "group", "context"],
)
def test_an_error_printed_with_an_echoing_one_is_replaced(error: RuntimeError) -> None:
    """The text leaves with the chain, not with the error on top of it."""
    assert SECRET in "".join(traceback.format_exception(error))

    stand_in = reportable(error)

    assert SECRET not in "".join(traceback.format_exception(stand_in))
    assert str(stand_in) == "RuntimeError (from DBAPIError driver=DriverError)"


def test_a_suppressed_chain_is_not_printed_and_not_replaced() -> None:
    error = wrapping(None, context=True)
    error.__suppress_context__ = True

    assert reportable(error) is error


def test_a_cycle_in_the_chain_ends() -> None:
    first, second = RuntimeError("first"), RuntimeError("second")
    first.__cause__, second.__cause__ = second, first

    assert reportable(first) is first
