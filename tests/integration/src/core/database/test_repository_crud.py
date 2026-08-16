"""Repository behaviour that only real SQL can prove.

Soft-delete visibility, the `ilike` search `ListQuery` builds and the primary-key
tiebreaker appended to every ORDER BY are all properties of the emitted statement, not of
the Python around it — a fake session would assert nothing about them.
"""

from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database.query import ListQuery
from src.core.database.repositories import SoftDeleteRepository
from src.core.pagination import PaginationParams, make_paginated_response
from src.core.utils.security import password_hasher
from src.user.enums import UserRole
from src.user.models import User
from src.user.repositories import UserRepository

pytestmark = pytest.mark.asyncio(loop_scope="session")

# Argon2 hashing costs ~100ms per call by design, and every row needs a hash the
# model's validator accepts. One hash for the whole module is enough — no test asserts
# anything about the value.
PASSWORD_HASH = password_hasher.hash("integration-password")


class SearchableUserRepository(SoftDeleteRepository[User]):
    """`UserRepository` with the list-query columns opted in.

    The shipped repository declares none, deliberately: `search` and `order_by` come from
    client input, so a domain has to allow a column before a client can reach it. That
    leaves the SQL `ListQuery` builds with nowhere to be exercised, so the opt-in lives
    here instead of being added to the template's default.
    """

    model = User
    searchable_fields = ("first_name", "last_name", "email", "username")
    sortable_fields = ("username", "created_at")
    default_order_by = "created_at"


def build_user_data(username: str, **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
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
    data.update(overrides)
    return data


async def test_create_read_update_and_soft_delete(db_session: AsyncSession) -> None:
    repository = UserRepository()
    username = f"crud-{uuid4().hex[:8]}"

    created = await repository.create(db_session, build_user_data(username))
    await db_session.flush()

    fetched = await repository.get_single(db_session, id=created.id)
    assert fetched is not None
    assert fetched.username == username

    updated = await repository.update(
        db_session, {"first_name": "Grace"}, id=created.id
    )
    await db_session.flush()
    assert updated is not None
    assert updated.first_name == "Grace"

    deleted = await repository.delete(db_session, id=created.id)
    await db_session.flush()
    assert deleted is not None
    assert deleted.is_deleted is True
    assert deleted.deleted_at is not None

    assert await repository.get_single(db_session, id=created.id) is None
    assert await repository.exists(db_session, id=created.id) is False
    assert created.id not in {
        user.id for user in await repository.get_list(db_session, username=username)
    }
    # The row is still there — the default filter is what hides it.
    assert await repository.get_single(db_session, id=created.id, is_deleted=True)


async def test_search_matches_substrings_case_insensitively(
    db_session: AsyncSession,
) -> None:
    repository = SearchableUserRepository()
    marker = uuid4().hex[:8]
    matching = [f"zoe{marker}", f"zoe{marker}x"]
    for username in [*matching, f"other{marker}"]:
        await repository.create(db_session, build_user_data(username))
    await db_session.flush()

    items, total = await repository.get_paginated_list(
        db_session,
        page=1,
        size=10,
        query=ListQuery(search=f"ZOE{marker}"),
    )

    assert total == 2
    assert {user.username for user in items} == set(matching)


async def test_pagination_over_a_non_unique_sort_column_is_stable(
    db_session: AsyncSession,
) -> None:
    """Every row shares one `created_at`, so only the tiebreaker keeps the pages disjoint.

    `created_at` defaults to `now()`, which PostgreSQL freezes at the start of the
    transaction: all five rows below are inserted with the identical value. Ordering by
    it alone leaves the row order undefined, and limit/offset pagination would duplicate
    some rows and skip others between pages. The primary key appended by
    `ListQuery.build_order_by` is what makes the sequence total.
    """
    repository = SearchableUserRepository()
    marker = uuid4().hex[:8]
    created = [
        await repository.create(db_session, build_user_data(f"page{index}-{marker}"))
        for index in range(5)
    ]
    await db_session.flush()

    query = ListQuery(search=marker, order_by="created_at", order="asc")
    pagination = PaginationParams(page=1, size=2)
    collected = []
    page_sizes = []
    for page in (1, 2, 3):
        items, total = await repository.get_paginated_list(
            db_session, page=page, size=pagination.size, query=query
        )
        assert total == 5
        collected.extend(item.id for item in items)
        page_sizes.append(len(items))

    created_at_values = {user.created_at for user in created}
    assert len(created_at_values) == 1
    assert page_sizes == [2, 2, 1]
    # PostgreSQL orders a `uuid` column by its bytes, which is the same order Python's
    # UUID comparison gives — so the pages, concatenated, must be the sorted ids.
    assert collected == sorted(user.id for user in created)
    assert make_paginated_response(items=[], total=5, pagination=pagination).pages == 3


async def test_soft_deleted_rows_leave_the_default_listing(
    db_session: AsyncSession,
) -> None:
    repository = SearchableUserRepository()
    marker = uuid4().hex[:8]
    kept = await repository.create(db_session, build_user_data(f"kept-{marker}"))
    removed = await repository.create(db_session, build_user_data(f"gone-{marker}"))
    await db_session.flush()

    await repository.delete(db_session, id=removed.id)
    await db_session.flush()

    items, total = await repository.get_paginated_list(
        db_session, page=1, size=10, query=ListQuery(search=marker)
    )

    assert total == 1
    assert [user.id for user in items] == [kept.id]
