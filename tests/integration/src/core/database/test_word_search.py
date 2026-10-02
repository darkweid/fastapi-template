"""The list search matches word by word: each word in some searchable
column, in any order. A full name typed into a list spans two columns, and a
match of the whole phrase against each column alone finds nobody. Text a row
shows from a parent table is searched through a `RelatedSearch` join."""

from collections.abc import Sequence
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database.query import ListQuery, RelatedSearch
from src.core.database.repositories import SoftDeleteRepository
from src.note.models import Note
from src.user.models import User
from tests.integration.src.core.database.test_repository_crud import (
    SearchableUserRepository,
    build_user_data,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


class NoteByOwnerRepository(SoftDeleteRepository[Note]):
    """Notes searched by their title and by their owner's name."""

    model = Note
    searchable_fields = ("title",)

    def _related_search(self) -> Sequence[RelatedSearch]:
        return [
            RelatedSearch(
                User, User.id == Note.owner_id, (User.first_name, User.last_name)
            )
        ]


async def test_full_name_matches_across_first_and_last_name(
    db_session: AsyncSession,
) -> None:
    repository = SearchableUserRepository()
    marker = uuid4().hex[:8]
    wanted = await repository.create(
        db_session,
        build_user_data(f"a{marker}", first_name=f"Ivan{marker}", last_name="Petrov"),
    )
    await repository.create(
        db_session,
        build_user_data(f"b{marker}", first_name=f"Ivan{marker}", last_name="Sidorov"),
    )
    await db_session.flush()

    for search in (f"Ivan{marker} Petrov", f"petrov  IVAN{marker}"):
        items, total = await repository.get_paginated_list(
            db_session, page=1, size=10, query=ListQuery(search=search)
        )
        assert total == 1
        assert items[0].id == wanted.id


async def test_related_search_mixes_own_and_joined_columns(
    db_session: AsyncSession,
) -> None:
    """One word may match the note's own title and another its owner, and the
    count carries the join the page does."""
    marker = uuid4().hex[:8]
    users = SearchableUserRepository()
    owner = await users.create(
        db_session, build_user_data(f"o{marker}", first_name=f"Grace{marker}")
    )
    other = await users.create(db_session, build_user_data(f"x{marker}"))
    await db_session.flush()
    notes = NoteByOwnerRepository()
    wanted = await notes.create(
        db_session, {"owner_id": owner.id, "title": f"Budget {marker}"}
    )
    await notes.create(db_session, {"owner_id": other.id, "title": f"Budget {marker}"})
    await db_session.flush()

    items, total = await notes.get_paginated_list(
        db_session,
        page=1,
        size=10,
        query=ListQuery(search=f"grace{marker} budget"),
    )

    assert total == 1
    assert items[0].id == wanted.id


async def test_search_reads_yo_as_ye(db_session: AsyncSession) -> None:
    """People type `е` for `ё` and the other way round; both find the row."""
    repository = SearchableUserRepository()
    marker = uuid4().hex[:8]
    user = await repository.create(
        db_session, build_user_data(f"y{marker}", first_name=f"Алёна{marker}")
    )
    await db_session.flush()

    for search in (f"АЛЕНА{marker}", f"алёна{marker}"):
        items, total = await repository.get_paginated_list(
            db_session, page=1, size=10, query=ListQuery(search=search)
        )
        assert total == 1
        assert items[0].id == user.id
