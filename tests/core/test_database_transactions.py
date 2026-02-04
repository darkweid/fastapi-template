from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.core.database.transactions import (
    _string_to_int64,
    advisory_xact_lock,
    maybe_begin,
    safe_begin,
    try_advisory_xact_lock,
)
from tests.fakes.db import AsyncTransactionContext, FakeAsyncSession


class FakeScalarResult:
    def __init__(self, value: int) -> None:
        self._value = value

    def scalar_one(self) -> int:
        return self._value


class TrackingSession(FakeAsyncSession):
    def __init__(self, in_transaction: bool = False) -> None:
        super().__init__(in_transaction=in_transaction)
        self.begin_called = 0
        self.begin_nested_called = 0

    def begin(self) -> AsyncTransactionContext:
        self.begin_called += 1
        return super().begin()

    def begin_nested(self) -> AsyncTransactionContext:
        self.begin_nested_called += 1
        return super().begin_nested()


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
async def test_maybe_begin_starts_transaction_when_needed() -> None:
    session = TrackingSession(in_transaction=False)

    async with maybe_begin(session):
        assert session.in_transaction() is True

    assert session.in_transaction() is False
    assert session.begin_called == 1
    assert session.begin_nested_called == 0


@pytest.mark.asyncio
async def test_maybe_begin_skips_when_already_in_transaction() -> None:
    session = TrackingSession(in_transaction=True)

    async with maybe_begin(session):
        assert session.in_transaction() is True

    assert session.in_transaction() is True
    assert session.begin_called == 0
    assert session.begin_nested_called == 0


@pytest.mark.asyncio
async def test_safe_begin_uses_nested_transaction_when_active() -> None:
    session = TrackingSession(in_transaction=True)

    async with safe_begin(session):
        assert session.in_transaction() is True

    assert session.begin_called == 0
    assert session.begin_nested_called == 1


@pytest.mark.asyncio
async def test_safe_begin_uses_regular_transaction_when_inactive() -> None:
    session = TrackingSession(in_transaction=False)

    async with safe_begin(session):
        assert session.in_transaction() is True

    assert session.in_transaction() is False
    assert session.begin_called == 1
    assert session.begin_nested_called == 0
