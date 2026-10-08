from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.core.database.uow.application import ApplicationUnitOfWork, get_uow
from src.core.database.uow.sqlalchemy import SQLAlchemyUnitOfWork
from tests.fakes.db import FakeAsyncSession


@pytest.mark.asyncio
async def test_sqlalchemy_uow_commit_marks_completed() -> None:
    session = FakeAsyncSession()
    uow = SQLAlchemyUnitOfWork(session)

    await uow.commit()

    assert uow.completed is True
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_sqlalchemy_uow_commit_twice_raises() -> None:
    session = FakeAsyncSession()
    uow = SQLAlchemyUnitOfWork(session)

    await uow.commit()

    with pytest.raises(RuntimeError):
        await uow.commit()


@pytest.mark.asyncio
async def test_sqlalchemy_uow_rollback_marks_completed() -> None:
    session = FakeAsyncSession()
    uow = SQLAlchemyUnitOfWork(session)

    await uow.rollback()

    assert uow.completed is True
    session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_sqlalchemy_uow_rollback_twice_raises() -> None:
    session = FakeAsyncSession()
    uow = SQLAlchemyUnitOfWork(session)

    await uow.rollback()

    with pytest.raises(RuntimeError):
        await uow.rollback()


@pytest.mark.asyncio
async def test_sqlalchemy_uow_flush_delegates_to_session() -> None:
    session = FakeAsyncSession()
    uow = SQLAlchemyUnitOfWork(session)

    await uow.flush()

    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_sqlalchemy_uow_refresh_delegates_to_session() -> None:
    session = FakeAsyncSession()
    uow = SQLAlchemyUnitOfWork(session)
    instance = object()

    await uow.refresh(instance)

    session.refresh.assert_awaited_once_with(
        instance,
        attribute_names=None,
        with_for_update=None,
    )


@pytest.mark.asyncio
async def test_sqlalchemy_uow_flush_after_commit_raises() -> None:
    session = FakeAsyncSession()
    uow = SQLAlchemyUnitOfWork(session)

    await uow.commit()

    with pytest.raises(RuntimeError):
        await uow.flush()

    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_sqlalchemy_uow_refresh_after_rollback_raises() -> None:
    session = FakeAsyncSession()
    uow = SQLAlchemyUnitOfWork(session)
    instance = object()

    await uow.rollback()

    with pytest.raises(RuntimeError):
        await uow.refresh(instance)

    session.refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_sqlalchemy_uow_aexit_rolls_back_on_exception() -> None:
    session = FakeAsyncSession()
    uow = SQLAlchemyUnitOfWork(session)

    await uow.__aenter__()
    await uow.__aexit__(RuntimeError, RuntimeError("fail"), None)

    session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_sqlalchemy_uow_aexit_skips_rollback_when_completed() -> None:
    session = FakeAsyncSession()
    uow = SQLAlchemyUnitOfWork(session)

    await uow.__aenter__()
    await uow.commit()
    await uow.__aexit__(RuntimeError, RuntimeError("fail"), None)

    session.rollback.assert_not_awaited()


@pytest.mark.asyncio
async def test_sqlalchemy_uow_clean_exit_without_commit_rolls_back() -> None:
    session = FakeAsyncSession()
    uow = SQLAlchemyUnitOfWork(session)

    async with uow:
        pass

    session.rollback.assert_awaited_once()
    session.commit.assert_not_awaited()
    assert uow.completed is True


@pytest.mark.asyncio
async def test_sqlalchemy_uow_clean_exit_skips_after_commit_hooks() -> None:
    session = FakeAsyncSession()
    uow = SQLAlchemyUnitOfWork(session)
    hook = AsyncMock()

    async with uow:
        uow.add_after_commit_hook(hook)

    hook.assert_not_awaited()
    session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_sqlalchemy_uow_commit_then_clean_exit_does_not_roll_back() -> None:
    session = FakeAsyncSession()
    uow = SQLAlchemyUnitOfWork(session)

    async with uow:
        await uow.commit()

    session.rollback.assert_not_awaited()


def test_application_uow_caches_repositories() -> None:
    session = FakeAsyncSession()
    uow = ApplicationUnitOfWork(session)

    first = uow.users
    second = uow.users

    assert first is second


@pytest.mark.asyncio
async def test_get_uow_returns_application_uow() -> None:
    session = FakeAsyncSession()
    uow = await get_uow(session)

    assert isinstance(uow, ApplicationUnitOfWork)


@pytest.mark.asyncio
async def test_sqlalchemy_uow_refuses_a_session_already_in_a_transaction() -> None:
    """A SAVEPOINT here would let `commit()` commit the caller's open
    transaction too, work the UoW never saw included."""
    session = FakeAsyncSession(in_transaction=True)
    uow = SQLAlchemyUnitOfWork(session)

    with pytest.raises(RuntimeError, match="already in a transaction"):
        async with uow:
            pass

    assert session.in_transaction() is True
    assert "uow_active" not in session.info
    session.rollback.assert_not_awaited()
    assert uow.completed is False


@pytest.mark.asyncio
async def test_sqlalchemy_uow_inside_a_uow_raises_and_keeps_the_outer_guard() -> None:
    """The inner exit used to pop `uow_active`, silently lifting the outer
    UoW's protection against `commit=True` repository calls."""
    session = FakeAsyncSession()

    async with SQLAlchemyUnitOfWork(session) as outer:
        with pytest.raises(RuntimeError, match="already in a transaction"):
            async with SQLAlchemyUnitOfWork(session):
                pass
        assert session.info.get("uow_active") is True
        await outer.commit()


@pytest.mark.asyncio
async def test_sqlalchemy_uow_sequential_units_on_one_session() -> None:
    session = FakeAsyncSession()

    async with SQLAlchemyUnitOfWork(session) as first:
        await first.commit()
    async with SQLAlchemyUnitOfWork(session):
        pass
    async with SQLAlchemyUnitOfWork(session) as third:
        await third.commit()

    assert session.commit.await_count == 2
    session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_sqlalchemy_uow_survives_a_rolled_back_savepoint() -> None:
    """The event log rolls its SAVEPOINT back on a failed insert; the unit of
    work around it must still be able to commit."""
    session = FakeAsyncSession()

    async with SQLAlchemyUnitOfWork(session) as uow:
        savepoint = await session.begin_nested()
        await savepoint.rollback()
        assert session.in_transaction() is True
        await uow.commit()

    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_sqlalchemy_uow_refuses_to_reenter_after_completion() -> None:
    """`get_unit_of_work` is cached per request, so two use cases in one route
    share one instance; a second entry would open a transaction nothing ends."""
    session = FakeAsyncSession()
    uow = SQLAlchemyUnitOfWork(session)
    async with uow:
        await uow.commit()

    with pytest.raises(RuntimeError, match="already been completed"):
        async with uow:
            pass

    assert session.in_transaction() is False
    assert "uow_active" not in session.info
