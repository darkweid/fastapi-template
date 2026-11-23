import traceback
from typing import Any

from aiohttp import ClientResponseError, RequestInfo
from aiosmtplib import SMTPDataError, SMTPRecipientRefused, SMTPRecipientsRefused
from asyncpg.exceptions import (
    InterfaceError as AsyncpgInterfaceError,
    UniqueViolationError,
)
from botocore.exceptions import ClientError
from fastapi.exceptions import ResponseValidationError
from multidict import CIMultiDict, CIMultiDictProxy
from pydantic import (
    AliasChoices,
    AliasPath,
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
)
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
from yarl import URL

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


RAW_CLIENT_ERRORS = [
    ClientResponseError(
        RequestInfo(
            URL(f"https://api.invalid/{SECRET}"), "GET", CIMultiDictProxy(CIMultiDict())
        ),
        (),
        status=500,
    ),
    UniqueViolationError(f"duplicate key ({SECRET})"),
    AsyncpgInterfaceError(f"cannot use {SECRET}"),
]

ECHOING = [
    database_error(),
    StatementError(f"bad value {SECRET}", "SELECT 1", None, None),
    PendingRollbackError(f"Original exception was: {SECRET}"),
    validation_error(),
    ResponseValidationError([{"loc": ("response",), "input": SECRET}]),
    SMTPRecipientsRefused([SMTPRecipientRefused(550, "no such user", SECRET)]),
    ResponseError(f"unknown command 'SET {SECRET}'"),
    SMTPDataError(550, f"no such user {SECRET}"),
    *RAW_CLIENT_ERRORS,
]


@pytest.mark.parametrize(
    "sqlstate", ["08001", "08006", "08P01", "57P01", "57P02", "57P03", "57P05", "53300"]
)
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


@pytest.mark.parametrize("cls", [DBAPIError, OperationalError])
def test_a_dropped_database_is_not_an_outage(cls: type[DBAPIError]) -> None:
    """The server closes the connection too, so the driver calls it a disconnect."""
    error = cls("q", None, DriverError("57P04"), connection_invalidated=True)

    assert is_database_outage(error) is False


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


@pytest.mark.parametrize(
    "cause", RAW_CLIENT_ERRORS, ids=lambda error: type(error).__name__
)
def test_a_client_error_under_another_one_is_replaced(cause: Exception) -> None:
    stand_in = reportable(wrapping(cause))

    assert str(stand_in) == f"RuntimeError (from {type(cause).__name__})"
    assert SECRET not in "".join(traceback.format_exception(stand_in))


def test_a_suppressed_chain_is_not_printed_and_not_replaced() -> None:
    error = wrapping(None, context=True)
    error.__suppress_context__ = True

    assert reportable(error) is error


def test_a_cycle_in_the_chain_ends() -> None:
    first, second = RuntimeError("first"), RuntimeError("second")
    first.__cause__, second.__cause__ = second, first

    assert reportable(first) is first


def test_a_chain_longer_than_the_recursion_limit_is_walked_to_its_end() -> None:
    error: BaseException = database_error()
    for _ in range(1500):
        outer = RuntimeError("retry")
        outer.__cause__ = error
        error = outer

    assert str(reportable(error)) == (
        "RuntimeError (from DBAPIError driver=DriverError)"
    )


def test_a_cycle_through_a_group_ends() -> None:
    member = RuntimeError("member")
    group = ExceptionGroup("several", [member])
    member.__context__ = group

    assert reportable(group) is group


def test_a_caller_names_what_echoes_in_its_own_work() -> None:
    error = wrapping(ValueError(SECRET))

    assert reportable(error) is error
    assert str(reportable(error, (ValueError,))) == "RuntimeError (from ValueError)"


def test_a_refusal_by_a_mail_server_is_named_by_its_code() -> None:
    assert describe_error(SMTPDataError(550, SECRET)) == "SMTPDataError code=550"
    unknown = SMTPDataError(f"550 {SECRET}", "refused")  # type: ignore[arg-type]
    assert describe_error(unknown) == "SMTPDataError"


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("NoSuchKey", "ClientError code=NoSuchKey"),
        ("404", "ClientError code=404"),
        (SECRET, "ClientError"),
        ("a\nb", "ClientError"),
        ("NoSuchKey\n", "ClientError"),
        ("x" * 65, "ClientError"),
        (404, "ClientError"),
        (None, "ClientError"),
    ],
)
def test_an_s3_refusal_is_named_by_a_safe_code_only(
    code: object, expected: str
) -> None:
    response: Any = {"Error": {"Code": code, "Message": SECRET}}

    assert describe_error(ClientError(response, "GetObject")) == expected


def test_an_s3_refusal_without_an_error_part_is_named_by_its_class() -> None:
    assert describe_error(ClientError({}, "GetObject")) == "ClientError"


class Line(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: int = 0
    unit: int = Field(default=0, alias="unitName")
    total: int = Field(
        default=0, validation_alias=AliasChoices("sumTotal", AliasPath("nested", 0))
    )


class Order(BaseModel):
    lines: list[Line] = []
    by_email: dict[str, Line] = {}
    by_phone: dict[int, int] = {}


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"lines": [{"amount": "x"}]}, "lines.*.amount: int_parsing"),
        ({"lines": [{"unitName": "x"}]}, "lines.*.unitName: int_parsing"),
        ({"lines": [{"sumTotal": "x"}]}, "lines.*.sumTotal: int_parsing"),
        ({"lines": [{SECRET: 1}]}, "lines.*.*: extra_forbidden"),
        ({"by_email": {SECRET: {"amount": "x"}}}, "by_email.*.amount: int_parsing"),
        ({"by_email": {SECRET: {SECRET: 1}}}, "by_email.*.*: extra_forbidden"),
        ({"by_phone": {998901234567: "x"}}, "by_phone.*: int_parsing"),
        ({"by_phone": {SECRET: 1}}, "by_phone.*.*: int_parsing"),
    ],
)
def test_a_location_holds_declared_names_only(payload: Any, expected: str) -> None:
    """A dict key and a forbidden extra field are the caller's own text."""
    with pytest.raises(ValidationError) as caught:
        Order.model_validate(payload)

    assert describe_error(caught.value) == f"Order: {expected}"
