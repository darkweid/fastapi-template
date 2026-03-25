from __future__ import annotations

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
