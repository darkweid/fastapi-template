from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database.uow import ApplicationUnitOfWork
from src.core.utils.security import password_hasher
from src.user.enums import UserRole
from src.user.models import User
from src.user.repositories import UserRepository

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _user_data(tag: str) -> dict[str, Any]:
    return {
        "first_name": "Clean",
        "last_name": "Exit",
        "email": f"{tag}@example.com",
        "username": tag,
        "phone_number": f"+1{uuid4().int % 10**10:010d}",
        "password_hash": password_hasher.hash("password"),
        "role": UserRole.VIEWER,
        "is_verified": True,
        "is_active": True,
    }


async def test_clean_exit_without_commit_persists_nothing_on_fresh_session(
    db_session: AsyncSession,
) -> None:
    tag = f"uow-clean-{uuid4().hex[:12]}"
    uow = ApplicationUnitOfWork(db_session)
    async with uow:
        await uow.users.create(uow.session, data=_user_data(tag))

    found = await db_session.scalar(select(User).where(User.username == tag))
    assert found is None


async def test_entering_after_a_raw_read_raises(db_session: AsyncSession) -> None:
    """A plain SELECT on the shared session autobegins a transaction; the UoW
    used to nest a SAVEPOINT in it and commit it on `commit()`."""
    await db_session.execute(select(1))

    with pytest.raises(RuntimeError, match="already in a transaction"):
        async with ApplicationUnitOfWork(db_session):
            pass

    assert db_session.in_transaction()
    assert "uow_active" not in db_session.info


async def test_entering_after_staged_work_raises_and_leaves_the_session_as_it_was(
    db_session: AsyncSession,
) -> None:
    """The refusal must leave the caller's staged work and transaction
    untouched: no rollback, no marker left behind."""
    tag = f"uow-staged-{uuid4().hex[:12]}"
    await UserRepository().create(db_session, data=_user_data(tag))
    await db_session.flush()

    with pytest.raises(RuntimeError, match="already in a transaction"):
        async with ApplicationUnitOfWork(db_session):
            pass

    assert db_session.in_transaction()
    assert "uow_active" not in db_session.info
    staged = await db_session.scalar(select(User).where(User.username == tag))
    assert staged is not None


async def test_a_unit_of_work_inside_a_unit_of_work_raises(
    db_session: AsyncSession,
) -> None:
    """The inner exit used to pop `uow_active` and lift the outer UoW's guard
    against a repository `commit=True`."""
    tag = f"uow-outer-{uuid4().hex[:12]}"
    outer = ApplicationUnitOfWork(db_session)
    async with outer:
        await outer.users.create(outer.session, data=_user_data(tag))
        with pytest.raises(RuntimeError, match="already in a transaction"):
            async with ApplicationUnitOfWork(db_session):
                pass
        assert db_session.info.get("uow_active") is True
        with pytest.raises(RuntimeError, match="inside an active UnitOfWork"):
            await outer.users.create(
                outer.session, data=_user_data(f"{tag}-x"), commit=True
            )

    found = await db_session.scalar(select(User).where(User.username == tag))
    assert found is None


async def test_sequential_units_of_work_on_one_session(
    db_session: AsyncSession,
) -> None:
    """The guard must not fire on a session the previous unit already ended."""
    first_tag = f"uow-first-{uuid4().hex[:12]}"
    second_tag = f"uow-second-{uuid4().hex[:12]}"

    first = ApplicationUnitOfWork(db_session)
    async with first:
        await first.users.create(first.session, data=_user_data(first_tag))
        await first.rollback()
    assert not db_session.in_transaction()

    second = ApplicationUnitOfWork(db_session)
    async with second:
        await second.users.create(second.session, data=_user_data(second_tag))
        await second.flush()
        found = await second.users.get_single(second.session, username=second_tag)
        assert found is not None

    assert not db_session.in_transaction()
    gone = await db_session.scalar(select(User).where(User.username == second_tag))
    assert gone is None
