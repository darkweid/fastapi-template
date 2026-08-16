"""UnitOfWork transaction boundaries against a real transaction.

Commit durability and rollback are properties of PostgreSQL, not of the UoW object: only a
second connection can tell whether the first one's work actually landed.
"""

from collections.abc import AsyncGenerator
from typing import Any
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from src.core.database.uow.abstract import RepositoryProtocol
from src.core.database.uow.application import ApplicationUnitOfWork
from src.core.utils.security import password_hasher
from src.user.enums import UserRole
from src.user.models import User
from src.user.repositories import UserRepository

pytestmark = pytest.mark.asyncio(loop_scope="session")

PASSWORD_HASH = password_hasher.hash("integration-password")


def build_user_data(username: str) -> dict[str, Any]:
    return {
        "first_name": "Ada",
        "last_name": "Lovelace",
        "email": f"{username}@example.com",
        "username": username,
        "phone_number": "+10000000000",
        "password_hash": PASSWORD_HASH,
        "role": UserRole.VIEWER,
        "is_verified": True,
        "is_active": True,
    }


@pytest_asyncio.fixture(loop_scope="session")
async def committed_user_ids(
    integration_engine: AsyncEngine,
) -> AsyncGenerator[list[UUID]]:
    """Ids of rows a test committed on purpose, removed afterwards.

    The `db_session` fixture's rollback cannot undo these — they are the point of the
    test — so they have to be deleted, or they stay visible to every later test.
    """
    user_ids: list[UUID] = []
    yield user_ids
    if user_ids:
        async with (
            AsyncSession(integration_engine) as session,
            session.begin(),
        ):
            await session.execute(delete(User).where(User.id.in_(user_ids)))


async def test_commit_is_visible_to_another_session(
    integration_engine: AsyncEngine, committed_user_ids: list[UUID]
) -> None:
    username = f"commit-{uuid4().hex[:8]}"

    async with AsyncSession(integration_engine, expire_on_commit=False) as session:
        uow: ApplicationUnitOfWork[RepositoryProtocol] = ApplicationUnitOfWork(session)
        async with uow:
            created = await uow.users.create(uow.session, build_user_data(username))
            await uow.flush()
            committed_user_ids.append(created.id)
            await uow.commit()

    async with AsyncSession(integration_engine) as other_session:
        persisted = await UserRepository().get_single(other_session, username=username)

    assert persisted is not None
    assert persisted.id == created.id


async def test_rollback_leaves_nothing_behind(
    integration_engine: AsyncEngine,
) -> None:
    username = f"rollback-{uuid4().hex[:8]}"

    async with AsyncSession(integration_engine, expire_on_commit=False) as session:
        uow: ApplicationUnitOfWork[RepositoryProtocol] = ApplicationUnitOfWork(session)
        async with uow:
            await uow.users.create(uow.session, build_user_data(username))
            await uow.flush()
            await uow.rollback()

    async with AsyncSession(integration_engine) as other_session:
        persisted = await UserRepository().get_single(other_session, username=username)

    assert persisted is None


async def test_repository_commit_inside_a_uow_is_refused(
    db_session: AsyncSession, integration_engine: AsyncEngine
) -> None:
    """The guard has to hold on a real session, where a stray COMMIT is irreversible."""
    uow: ApplicationUnitOfWork[RepositoryProtocol] = ApplicationUnitOfWork(db_session)
    username = f"guard-{uuid4().hex[:8]}"

    async with uow:
        with pytest.raises(RuntimeError, match="inside an active UnitOfWork"):
            await uow.users.create(uow.session, build_user_data(username), commit=True)
        # The refusal left the UoW's own transaction usable.
        await uow.commit()

    # Committing above would have made the row durable had the guard let the write
    # through, so its absence is what proves the refusal happened before the INSERT.
    async with AsyncSession(integration_engine) as other_session:
        persisted = await UserRepository().get_single(other_session, username=username)

    assert persisted is None
