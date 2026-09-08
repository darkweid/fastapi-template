from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.core.database.transactions import (
    _string_to_int64,
    advisory_xact_lock,
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
