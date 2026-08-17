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


async def test_clean_exit_without_commit_discards_savepoint_work(
    db_session: AsyncSession,
) -> None:
    # Mimic the authenticated-request path: a prior SELECT autobegins the
    # outer transaction, so the UoW enters through begin_nested().
    await db_session.execute(select(1))
    tag = f"uow-nested-{uuid4().hex[:12]}"
    uow = ApplicationUnitOfWork(db_session)
    async with uow:
        await uow.users.create(uow.session, data=_user_data(tag))

    found = await db_session.scalar(select(User).where(User.username == tag))
    assert found is None
