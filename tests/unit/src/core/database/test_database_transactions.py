from __future__ import annotations

import sys
from unittest.mock import AsyncMock

import pytest

from src.core.database.transactions import (
    _string_to_int64,
    advisory_xact_lock,
    set_local_statement_timeout,
    try_advisory_xact_lock,
)
from tests.fakes.db import FakeAsyncSession


class FakeScalarResult:
    def __init__(self, value: int) -> None:
        self._value = value

    def scalar_one(self) -> int:
        return self._value


def test_string_to_int64_is_deterministic_and_in_range() -> None:
    value = _string_to_int64("lock-key")
    second = _string_to_int64("lock-key")

    assert value == second
    assert -(2**63) <= value < 2**63


@pytest.mark.asyncio
async def test_advisory_xact_lock_requires_active_transaction() -> None:
    session = FakeAsyncSession(in_transaction=False)

    with pytest.raises(RuntimeError):
        await advisory_xact_lock(session, "key")


@pytest.mark.asyncio
async def test_advisory_xact_lock_executes_when_in_transaction() -> None:
    session = FakeAsyncSession(in_transaction=True)
    session.execute = AsyncMock()

    await advisory_xact_lock(session, "key")

    session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_try_advisory_xact_lock_requires_active_transaction() -> None:
    session = FakeAsyncSession(in_transaction=False)

    with pytest.raises(RuntimeError):
        await try_advisory_xact_lock(session, "key")


@pytest.mark.asyncio
async def test_try_advisory_xact_lock_returns_true_on_acquire() -> None:
    session = FakeAsyncSession(in_transaction=True)
    session.execute = AsyncMock(return_value=FakeScalarResult(1))

    result = await try_advisory_xact_lock(session, "key")

    assert result is True


@pytest.mark.asyncio
async def test_try_advisory_xact_lock_returns_false_on_failure() -> None:
    session = FakeAsyncSession(in_transaction=True)
    session.execute = AsyncMock(return_value=FakeScalarResult(0))

    result = await try_advisory_xact_lock(session, "key")

    assert result is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "seconds", [0, -1, float("nan"), float("inf"), True, 2**31, sys.float_info.max]
)
async def test_set_local_statement_timeout_rejects_invalid_values(
    seconds: float,
) -> None:
    """Zero means "no limit" to PostgreSQL, the opposite of what the caller asked for."""
    session = FakeAsyncSession(in_transaction=True)

    with pytest.raises(ValueError):
        await set_local_statement_timeout(session, seconds)

    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_local_statement_timeout_requires_active_transaction() -> None:
    session = FakeAsyncSession(in_transaction=False)

    with pytest.raises(RuntimeError):
        await set_local_statement_timeout(session, 1)


@pytest.mark.asyncio
async def test_set_local_statement_timeout_rounds_up_to_whole_milliseconds() -> None:
    """A sub-millisecond request must not round down to 0, which disables the limit."""
    session = FakeAsyncSession(in_transaction=True)

    await set_local_statement_timeout(session, 0.0001)

    statement = session.execute.await_args.args[0]
    assert list(statement.compile().params.values()) == [
        "statement_timeout",
        "1",
        True,
    ]
