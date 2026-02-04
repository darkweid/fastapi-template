import pytest
from sqlalchemy.exc import IntegrityError

from src.core.middleware import PostgresqlErrorHandlingResult, handle_postgresql_error


class DummyNotNullViolation:
    sqlstate = "23502"
    detail = "Failing row contains (..., null, ...)."
    column_name = None

    def __str__(self) -> str:
        return 'null value in column "username" of relation "users" violates not-null constraint'


class DummyUniqueViolation:
    sqlstate = "23505"
    detail = "Key (email)=(user@example.com) already exists."

    def __str__(self) -> str:
        return "duplicate key"


class DummyForeignKeyViolation:
    sqlstate = "23503"
    detail = "Key (user_id)=(1) is not present in table."

    def __str__(self) -> str:
        return "violates foreign key"


class DummyForeignKeyViolationNoDetail:
    sqlstate = "23503"
    detail = None

    def __str__(self) -> str:
        return "ERROR: insert fails DETAIL: missing reference"


class DummyCheckViolation:
    sqlstate = "23514"
    detail = "Check constraint failed."

    def __str__(self) -> str:
        return "check violation"


class DummyUnknownViolation:
    sqlstate = "99999"
    detail = None

    def __str__(self) -> str:
        return "unknown error"


@pytest.mark.asyncio
async def test_handle_postgresql_error_not_null_returns_500() -> None:
    err = IntegrityError("msg", None, DummyNotNullViolation())  # type: ignore[arg-type]

    result: PostgresqlErrorHandlingResult = handle_postgresql_error(err)

    assert result.response.status_code == 500
    assert result.response.body == b'{"detail":"Unexpected error"}'


@pytest.mark.asyncio
async def test_handle_postgresql_error_unique_violation_returns_409() -> None:
    err = IntegrityError("msg", None, DummyUniqueViolation())  # type: ignore[arg-type]

    result = handle_postgresql_error(err)

    assert result.response.status_code == 409
    assert result.response.body == b'{"detail":"email"}'


@pytest.mark.asyncio
async def test_handle_postgresql_error_foreign_key_violation_returns_400() -> None:
    err = IntegrityError("msg", None, DummyForeignKeyViolation())  # type: ignore[arg-type]

    result = handle_postgresql_error(err)

    assert result.response.status_code == 400
    assert (
        result.response.body
        == b'{"detail":"Key (user_id)=(1) is not present in table."}'
    )


@pytest.mark.asyncio
async def test_handle_postgresql_error_detail_fallback_from_raw_message() -> None:
    err = IntegrityError(
        "msg", None, DummyForeignKeyViolationNoDetail()  # type: ignore[arg-type]
    )

    result = handle_postgresql_error(err)

    assert result.response.status_code == 400
    assert result.response.body == b'{"detail":"missing reference"}'


@pytest.mark.asyncio
async def test_handle_postgresql_error_check_violation_returns_500() -> None:
    err = IntegrityError("msg", None, DummyCheckViolation())  # type: ignore[arg-type]

    result = handle_postgresql_error(err)

    assert result.response.status_code == 500
    assert result.response.body == b'{"detail":"Unexpected error"}'


@pytest.mark.asyncio
async def test_handle_postgresql_error_unknown_violation_returns_500() -> None:
    err = IntegrityError("msg", None, DummyUnknownViolation())  # type: ignore[arg-type]

    result = handle_postgresql_error(err)

    assert result.response.status_code == 500
    assert result.response.body == b'{"detail":"Unexpected error"}'
