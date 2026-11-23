from __future__ import annotations

from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock, MagicMock

from fastapi import FastAPI
import pytest
from sqlalchemy.exc import InvalidRequestError

from src.core.database.session import get_unit_of_work
from src.event_log.actor import Actor
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
    """Each request of a route test gets a unit of its own over one session, so
    each exit has to leave that session idle for the next, as the real one does."""
    session = FakeAsyncSession()

    async with FakeUnitOfWork(session=session) as committed:
        assert session.in_transaction() is True
        assert session.info == {"uow_active": True}
        await committed.commit()
    assert session.in_transaction() is False

    async with FakeUnitOfWork(session=session) as abandoned:
        pass
    assert session.in_transaction() is False
    abandoned.rollback.assert_awaited_once()

    with pytest.raises(ValueError, match="boom"):
        async with FakeUnitOfWork(session=session) as failed:
            raise ValueError("boom")
    assert session.in_transaction() is False
    failed.commit.assert_not_awaited()
    assert session.info == {}


async def test_fake_unit_of_work_drops_hooks_on_a_clean_exit() -> None:
    hook = AsyncMock()

    async with FakeUnitOfWork() as uow:
        uow.add_after_commit_hook(hook)

    hook.assert_not_awaited()
    assert uow.completed is True


async def _enter(uow: FakeUnitOfWork) -> None:
    async with uow:
        pass


@pytest.mark.parametrize(
    "use",
    [
        _enter,
        lambda uow: uow.commit(),
        lambda uow: uow.rollback(),
        lambda uow: uow.flush(),
        lambda uow: uow.publish(Actor.system(), MagicMock()),
    ],
    ids=["enter", "commit", "rollback", "flush", "publish"],
)
@pytest.mark.parametrize("finish", ["commit", "rollback"])
async def test_a_completed_fake_unit_of_work_refuses_further_use(
    use: Callable[[FakeUnitOfWork], Awaitable[None]], finish: str
) -> None:
    """The real one refuses; a fake that did not would let a use case that
    reads after its commit pass every unit test."""
    uow = FakeUnitOfWork(repositories={"users": MagicMock()})
    async with uow:
        await getattr(uow, finish)()

    with pytest.raises(RuntimeError, match="already been completed"):
        await use(uow)
    with pytest.raises(RuntimeError, match="already been completed"):
        _ = uow.session
    with pytest.raises(RuntimeError, match="already been completed"):
        _ = uow.users


async def test_a_fresh_fake_unit_of_work_shares_everything_but_completion() -> None:
    users = MagicMock()
    first = FakeUnitOfWork(repositories={"users": users})
    event = MagicMock()
    async with first:
        await first.publish(Actor.system(), event)
        await first.commit()

    second = first.fresh()

    assert second.completed is False
    assert second.users is users
    async with second:
        await second.publish(Actor.system(), event)
    assert len(first.published) == 2


async def test_app_with_fakes_gives_every_request_its_own_unit_of_work(
    app_with_fakes: FastAPI,
) -> None:
    provider = app_with_fakes.dependency_overrides[get_unit_of_work]

    first = await anext(provider())
    second = await anext(provider())

    assert first is not second
    assert first.session is second.session
