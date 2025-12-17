import pytest
from sqlalchemy.exc import IntegrityError

from src.core.middleware import PostgresqlErrorHandlingResult, handle_postgresql_error


class DummyNotNullViolation:
    sqlstate = "23502"
    detail = "Failing row contains (..., null, ...)."
    column_name = None

    def __str__(self) -> str:
        return 'null value in column "username" of relation "users" violates not-null constraint'


@pytest.mark.asyncio
async def test_handle_postgresql_error_not_null_returns_500() -> None:
    err = IntegrityError("msg", None, DummyNotNullViolation())  # type: ignore[arg-type]

    result: PostgresqlErrorHandlingResult = handle_postgresql_error(err)

    assert result.response.status_code == 500
    assert result.response.body == b'{"detail":"Unexpected error"}'
