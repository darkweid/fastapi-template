"""The notes list past one page, and the index that serves it."""

from datetime import timedelta
import random
from uuid import uuid4

import pytest
from sqlalchemy import inspect as sqlalchemy_inspect, select, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from src.core.database.filters import FilterCondition
from src.core.database.query import ListQuery
from src.core.pagination import PaginationParams
from src.core.utils.datetime_utils import get_utc_now
from src.note.models import Note
from src.note.repositories import NoteRepository
from src.note.services import NoteService
from src.user.models import User
from tests.factories.user_factory import build_user
from tests.helpers.sql import count_statements

pytestmark = pytest.mark.asyncio(loop_scope="session")


def _owner() -> User:
    return build_user(
        email=f"{uuid4().hex}@example.com", username=f"notes-{uuid4().hex}"
    )


async def _page(
    session: AsyncSession, owner: User, page: int, size: int = 100
) -> tuple[list, int]:
    response = await NoteService(NoteRepository(), session).get_paginated_list(
        PaginationParams(page=page, size=size),
        query=ListQuery(conditions=FilterCondition(eq={"owner_id": owner.id})),
    )
    return [item.id for item in response.items], response.total


async def test_101_notes_are_each_reached_once(
    integration_engine: AsyncEngine, db_session: AsyncSession
) -> None:
    owner, stranger = _owner(), _owner()
    db_session.add_all([owner, stranger])
    await db_session.flush()
    started_at = get_utc_now() - timedelta(days=1)
    # Three rows share every instant, so only the id tiebreak separates them.
    notes = [
        Note(
            id=uuid4(),
            title="Title",
            owner_id=owner.id,
            created_at=started_at + timedelta(minutes=index // 3),
        )
        for index in range(101)
    ]
    expected = [
        note.id
        for note in sorted(
            notes, key=lambda note: (note.created_at, note.id), reverse=True
        )
    ]
    hidden = [
        Note(title="Foreign", owner_id=stranger.id),
        Note(
            title="Deleted",
            owner_id=owner.id,
            is_deleted=True,
            deleted_at=get_utc_now(),
        ),
    ]
    db_session.add_all(random.sample([*notes, *hidden], 103))
    await db_session.flush()

    with count_statements(integration_engine) as one_row:
        await _page(db_session, owner, page=1, size=1)
    with count_statements(integration_engine) as full_page:
        first, total = await _page(db_session, owner, page=1)
    second, second_total = await _page(db_session, owner, page=2)

    assert (len(first), len(second)) == (100, 1)
    assert first + second == expected
    assert total == second_total == 101
    assert len(full_page) == len(one_row)


async def test_the_owner_list_is_read_off_the_index_without_a_sort(
    integration_engine: AsyncEngine, db_session: AsyncSession
) -> None:
    async with integration_engine.connect() as connection:
        indexes = await connection.run_sync(
            lambda sync: sqlalchemy_inspect(sync).get_indexes("notes")
        )
    assert [
        index["column_names"]
        for index in indexes
        if "owner_id" in index["column_names"]
    ] == [["owner_id", "created_at", "id"]]

    repository = NoteRepository()
    statement = (
        select(Note)
        .where(Note.owner_id == uuid4(), Note.is_deleted.is_(False))
        .order_by(
            *ListQuery().build_order_by(
                Note, repository.sortable_fields, repository.default_order_by
            )
        )
        .limit(100)
    )
    compiled = statement.compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
    )
    for method in ("seqscan", "sort", "bitmapscan"):
        await db_session.execute(text(f"SET LOCAL enable_{method} = off"))
    plan = str(await db_session.scalar(text(f"EXPLAIN (FORMAT JSON) {compiled}")))

    assert "ix_notes_owner_created_at" in plan
    assert "Sort" not in plan
