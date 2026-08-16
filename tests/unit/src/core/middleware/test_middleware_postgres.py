import json

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


class DummyExclusionViolation:
    sqlstate = "23P01"
    detail = "Key (room_id, during)=(1, [09:00,10:00)) conflicts with existing key."

    def __str__(self) -> str:
        return "exclusion violation"


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
    assert json.loads(result.response.body) == {
        "code": "internal_error",
        "message": "Unexpected error",
    }


@pytest.mark.asyncio
async def test_handle_postgresql_error_unique_violation_returns_409() -> None:
    err = IntegrityError("msg", None, DummyUniqueViolation())  # type: ignore[arg-type]

    result = handle_postgresql_error(err)

    assert result.response.status_code == 409
    assert json.loads(result.response.body) == {
        "code": "already_exists",
        "message": "Resource already exists.",
    }


@pytest.mark.asyncio
async def test_unique_violation_does_not_leak_the_conflicting_value() -> None:
    err = IntegrityError("msg", None, DummyUniqueViolation())  # type: ignore[arg-type]

    result = handle_postgresql_error(err)

    body = json.loads(result.response.body)
    assert body == {"code": "already_exists", "message": "Resource already exists."}
    assert "user@example.com" not in result.response.body.decode()


@pytest.mark.asyncio
async def test_handle_postgresql_error_foreign_key_violation_returns_400() -> None:
    err = IntegrityError("msg", None, DummyForeignKeyViolation())  # type: ignore[arg-type]

    result = handle_postgresql_error(err)

    assert result.response.status_code == 400
    assert json.loads(result.response.body) == {
        "code": "invalid_reference",
        "message": "Referenced resource does not exist.",
    }
    assert "user_id" not in result.response.body.decode()


@pytest.mark.asyncio
async def test_handle_postgresql_error_detail_fallback_from_raw_message() -> None:
    err = IntegrityError(
        "msg", None, DummyForeignKeyViolationNoDetail()  # type: ignore[arg-type]
    )

    result = handle_postgresql_error(err)

    assert result.response.status_code == 400
    assert json.loads(result.response.body) == {
        "code": "invalid_reference",
        "message": "Referenced resource does not exist.",
    }


@pytest.mark.asyncio
async def test_handle_postgresql_error_check_violation_returns_500() -> None:
    err = IntegrityError("msg", None, DummyCheckViolation())  # type: ignore[arg-type]

    result = handle_postgresql_error(err)

    assert result.response.status_code == 500
    assert json.loads(result.response.body) == {
        "code": "internal_error",
        "message": "Unexpected error",
    }


@pytest.mark.asyncio
async def test_handle_postgresql_error_exclusion_violation_returns_500() -> None:
    err = IntegrityError("msg", None, DummyExclusionViolation())  # type: ignore[arg-type]

    result = handle_postgresql_error(err)

    assert result.response.status_code == 500
    assert json.loads(result.response.body) == {
        "code": "internal_error",
        "message": "Unexpected error",
    }
    assert "room_id" not in result.response.body.decode()


@pytest.mark.asyncio
async def test_handle_postgresql_error_unknown_violation_returns_500() -> None:
    err = IntegrityError("msg", None, DummyUnknownViolation())  # type: ignore[arg-type]

    result = handle_postgresql_error(err)

    assert result.response.status_code == 500
    assert json.loads(result.response.body) == {
        "code": "internal_error",
        "message": "Unexpected error",
    }
