from __future__ import annotations

import pytest
from sqlalchemy.exc import InvalidRequestError

from tests.fakes.db import FakeAsyncSession, FakeUnitOfWork


async def test_commit_ends_the_transaction() -> None:
    """A fake that stays "in transaction" after commit would make the UoW guard
    refuse every second unit of work on the same session."""
    session = FakeAsyncSession()
    await session.begin()

    await session.commit()

    assert session.in_transaction() is False


async def test_rollback_ends_the_transaction() -> None:
    session = FakeAsyncSession()
    await session.begin()

    await session.rollback()

    assert session.in_transaction() is False


async def test_savepoint_rollback_keeps_the_outer_transaction() -> None:
    """The savepoint forwards its rollback to `session.rollback` so tests can
    assert on it; that call must not end the transaction around it, or the
    event log's swallow path would look like it ended the unit of work."""
    session = FakeAsyncSession()
    await session.begin()
    savepoint = await session.begin_nested()

    await savepoint.rollback()

    assert session.in_transaction() is True
    session.rollback.assert_awaited_once()
    assert session.open_savepoints == 0


async def test_savepoint_block_exit_keeps_the_outer_transaction() -> None:
    session = FakeAsyncSession()
    await session.begin()

    async with session.begin_nested():
        assert session.open_savepoints == 1

    assert session.in_transaction() is True
    assert session.open_savepoints == 0


async def test_failed_savepoint_block_keeps_the_outer_transaction() -> None:
    session = FakeAsyncSession()
    await session.begin()
    session.fail_nested_with = RuntimeError("insert failed")

    with pytest.raises(RuntimeError, match="insert failed"):
        async with session.begin_nested():
            pass

    assert session.in_transaction() is True
    assert session.open_savepoints == 0


async def test_top_level_block_exit_ends_the_transaction() -> None:
    session = FakeAsyncSession()

    async with session.begin():
        assert session.in_transaction() is True

    assert session.in_transaction() is False


async def test_savepoint_on_an_idle_session_autobegins_the_outer_transaction() -> None:
    """A real session autobegins the outer transaction for `begin_nested()` and
    keeps it open after the savepoint ends; a fake that cleared the flag would
    hide a UoW entered after the event log wrote outside one."""
    session = FakeAsyncSession()

    async with session.begin_nested():
        pass

    assert session.in_transaction() is True
    assert session.open_savepoints == 0


async def test_session_commit_ends_the_transaction_with_a_savepoint_open() -> None:
    session = FakeAsyncSession()
    await session.begin()
    await session.begin_nested()

    await session.commit()

    assert session.in_transaction() is False
    assert session.open_savepoints == 0


async def test_session_rollback_ends_the_transaction_with_a_savepoint_open() -> None:
    session = FakeAsyncSession()
    await session.begin()
    savepoint = await session.begin_nested()

    await session.rollback()
    await savepoint.rollback()

    assert session.in_transaction() is False
    assert session.open_savepoints == 0


async def test_begin_inside_a_transaction_raises() -> None:
    """A real session refuses a second `begin()`; a fake that accepted it would
    let a nested unit of work look legal."""
    session = FakeAsyncSession(in_transaction=True)

    with pytest.raises(InvalidRequestError):
        await session.begin()

    assert session.in_transaction() is True


async def test_fake_unit_of_work_refuses_a_session_already_in_a_transaction() -> None:
    """Route tests run on the fake; without the real guard a handler that
    enters a unit of work after a raw read would pass them and fail in prod."""
    session = FakeAsyncSession(in_transaction=True)

    with pytest.raises(RuntimeError, match="already in a transaction"):
        async with FakeUnitOfWork(session=session):
            pass

    assert session.in_transaction() is True


async def test_fake_unit_of_work_ends_its_transaction_on_every_exit() -> None:
    """One fake unit of work serves every request of a route test, so each exit
    has to leave the session idle for the next entry, as the real one does."""
    session = FakeAsyncSession()
    uow = FakeUnitOfWork(session=session)

    async with uow:
        assert session.in_transaction() is True
        await uow.commit()
    assert session.in_transaction() is False

    async with uow:
        pass
    assert session.in_transaction() is False

    with pytest.raises(ValueError, match="boom"):
        async with uow:
            raise ValueError("boom")
    assert session.in_transaction() is False
