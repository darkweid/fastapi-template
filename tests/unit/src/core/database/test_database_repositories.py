from __future__ import annotations

from datetime import datetime, timedelta, timezone
import enum
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Integer,
    String,
    select,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, load_only, mapped_column, relationship

from src.core.database.base import Base as SQLAlchemyBase
from src.core.database.filters import FilterCondition
from src.core.database.query import ListQuery
from src.core.database.repositories import (
    BaseRepository,
    SoftDeleteRepository,
)
from src.core.errors.exceptions import FilteringError
from src.core.utils.datetime_utils import get_utc_now


class RepositoryModel(SQLAlchemyBase):
    __tablename__ = "repository_models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class NoSoftDeleteModel(SQLAlchemyBase):
    __tablename__ = "no_soft_delete_models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64))


class RepositoryModelRepository(BaseRepository[RepositoryModel]):
    model = RepositoryModel


class RepositorySoftDeleteRepository(SoftDeleteRepository[RepositoryModel]):
    model = RepositoryModel


class NoSoftDeleteRepository(SoftDeleteRepository[NoSoftDeleteModel]):
    model = NoSoftDeleteModel


class FakeScalars:
    def __init__(self, items: list[RepositoryModel]) -> None:
        self._items = items

    def first(self) -> RepositoryModel | None:
        return self._items[0] if self._items else None

    def all(self) -> list[RepositoryModel]:
        return list(self._items)


class FakeResult:
    def __init__(
        self,
        *,
        items: list[RepositoryModel] | None = None,
        scalar: int | None = None,
        rows: list[int] | None = None,
    ) -> None:
        self._items = items or []
        self._scalar = scalar
        self._rows = rows if rows is not None else []

    def unique(self) -> FakeResult:
        return self

    def scalars(self) -> FakeScalars:
        return FakeScalars(self._items)

    def scalar_one(self) -> int:
        if self._scalar is None:
            raise RuntimeError("scalar value is not set")
        return self._scalar

    def all(self) -> list[int]:
        return list(self._rows)


class FakeExecuteResult:
    def __init__(self, rowcount: int | None = None) -> None:
        self.rowcount = rowcount


class RepositorySession:
    def __init__(self) -> None:
        self.add = MagicMock()
        self.commit = AsyncMock()
        self.refresh = AsyncMock()
        self.rollback = AsyncMock()
        self.delete = AsyncMock()
        self.execute = AsyncMock()
        self.scalar = AsyncMock()
        # Mirrors real `AsyncSession.info`, consulted by the commit guard.
        self.info: dict[str, Any] = {}


FIXED_NOW = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def fixed_utc_now() -> datetime:
    return FIXED_NOW


@pytest.mark.asyncio
async def test_base_repository_create_staged() -> None:
    repo = RepositoryModelRepository()
    session = RepositorySession()

    instance = await repo.create(
        session=session,
        data={"name": "alpha"},
        commit=False,
    )

    assert isinstance(instance, RepositoryModel)
    session.add.assert_called_once_with(instance)
    session.commit.assert_not_awaited()
    session.refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_base_repository_create_commit_rolls_back_on_integrity_error() -> None:
    repo = RepositoryModelRepository()
    session = RepositorySession()
    session.commit.side_effect = IntegrityError("stmt", "params", "orig")

    with pytest.raises(IntegrityError):
        await repo.create(
            session=session,
            data={"name": "alpha"},
            commit=True,
        )

    session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_base_repository_exists_strict_single_true() -> None:
    repo = RepositoryModelRepository()
    session = RepositorySession()
    session.execute.return_value = FakeResult(rows=[1])

    exists = await repo.exists(session=session, strict_single=True, name="alpha")

    assert exists is True


@pytest.mark.asyncio
async def test_base_repository_exists_strict_single_false() -> None:
    repo = RepositoryModelRepository()
    session = RepositorySession()
    session.scalar.return_value = 1

    exists = await repo.exists(session=session, strict_single=False, name="alpha")

    assert exists is True


@pytest.mark.asyncio
async def test_base_repository_get_single_returns_first() -> None:
    repo = RepositoryModelRepository()
    session = RepositorySession()
    first = RepositoryModel(name="alpha")
    second = RepositoryModel(name="beta")
    session.execute.return_value = FakeResult(items=[first, second])

    result = await repo.get_single(session=session, name="alpha")

    assert result is first


@pytest.mark.asyncio
async def test_base_repository_get_single_applies_for_update_scope() -> None:
    repo = RepositoryModelRepository()
    session = RepositorySession()
    session.execute.return_value = FakeResult(items=[])

    await repo.get_single(session=session, for_update=True, id=1)

    query = session.execute.await_args.args[0]
    assert query._for_update_arg is not None
    assert query._for_update_arg.of == [RepositoryModel.__table__.c.id]


@pytest.mark.asyncio
async def test_base_repository_get_list_returns_all() -> None:
    repo = RepositoryModelRepository()
    session = RepositorySession()
    items = [RepositoryModel(name="alpha"), RepositoryModel(name="beta")]
    session.execute.return_value = FakeResult(items=items)

    result = await repo.get_list(session=session)

    assert result == items


@pytest.mark.asyncio
async def test_base_repository_get_list_applies_default_created_at_ordering() -> None:
    repo = RepositoryModelRepository()
    session = RepositorySession()
    session.execute.return_value = FakeResult(items=[])

    await repo.get_list(session=session)

    query = session.execute.await_args.args[0]
    order_by_clause = list(query._order_by_clauses)[0]
    assert str(order_by_clause) == "repository_models.created_at DESC"


@pytest.mark.asyncio
async def test_base_repository_apply_default_ordering_supports_ascending_order() -> (
    None
):
    repo = RepositoryModelRepository()

    query = repo._apply_default_ordering(select(RepositoryModel), order="asc")

    order_by_clause = list(query._order_by_clauses)[0]
    assert str(order_by_clause) == "repository_models.created_at ASC"


@pytest.mark.asyncio
async def test_base_repository_get_list_applies_for_update_scope() -> None:
    repo = RepositoryModelRepository()
    session = RepositorySession()
    session.execute.return_value = FakeResult(items=[])

    await repo.get_list(session=session, for_update=True)

    query = session.execute.await_args.args[0]
    assert query._for_update_arg is not None
    assert query._for_update_arg.of == [RepositoryModel.__table__.c.id]


@pytest.mark.asyncio
async def test_base_repository_get_paginated_list_validates_input() -> None:
    repo = RepositoryModelRepository()
    session = RepositorySession()

    with pytest.raises(ValueError):
        await repo.get_paginated_list(session=session, page=0, size=10)

    with pytest.raises(ValueError):
        await repo.get_paginated_list(session=session, page=1, size=0)


@pytest.mark.asyncio
async def test_base_repository_get_paginated_list_returns_items_and_total() -> None:
    repo = RepositoryModelRepository()
    session = RepositorySession()
    items = [RepositoryModel(name="alpha"), RepositoryModel(name="beta")]
    session.execute.side_effect = [
        FakeResult(items=items),
        FakeResult(scalar=5),
    ]

    result_items, total = await repo.get_paginated_list(session=session, page=1, size=2)

    assert result_items == items
    assert total == 5


@pytest.mark.asyncio
async def test_base_repository_get_paginated_list_applies_default_created_at_ordering() -> (
    None
):
    repo = RepositoryModelRepository()
    session = RepositorySession()
    session.execute.side_effect = [
        FakeResult(items=[]),
        FakeResult(scalar=0),
    ]

    await repo.get_paginated_list(session=session, page=1, size=10)

    query = session.execute.await_args_list[0].args[0]
    order_by_clauses = [str(clause) for clause in query._order_by_clauses]
    assert order_by_clauses == [
        "repository_models.created_at DESC NULLS LAST",
        "repository_models.id DESC",
    ]


class SearchableRepository(BaseRepository[RepositoryModel]):
    model = RepositoryModel
    searchable_fields = ("name",)
    sortable_fields = ("created_at", "name")
    default_order_by = "created_at"


@pytest.mark.asyncio
async def test_base_repository_get_paginated_list_applies_search_to_items_and_count() -> (
    None
):
    repo = SearchableRepository()
    session = RepositorySession()
    session.execute.side_effect = [
        FakeResult(items=[]),
        FakeResult(scalar=0),
    ]

    await repo.get_paginated_list(
        session=session, page=1, size=10, query=ListQuery(search="alpha")
    )

    items_query, count_query = (
        call.args[0] for call in session.execute.await_args_list
    )
    items_sql = str(items_query.compile(dialect=postgresql.dialect()))
    count_sql = str(count_query.compile(dialect=postgresql.dialect()))
    assert "ILIKE" in items_sql
    assert "ILIKE" in count_sql


@pytest.mark.asyncio
async def test_base_repository_get_paginated_list_keeps_exact_filters_with_query() -> (
    None
):
    repo = SearchableRepository()
    session = RepositorySession()
    session.execute.side_effect = [
        FakeResult(items=[]),
        FakeResult(scalar=0),
    ]

    await repo.get_paginated_list(
        session=session,
        page=1,
        size=10,
        query=ListQuery(conditions=FilterCondition(gte={"id": 5})),
        name="alpha",
    )

    items_query, count_query = (
        call.args[0] for call in session.execute.await_args_list
    )
    items_sql = str(items_query.compile(dialect=postgresql.dialect()))
    count_sql = str(count_query.compile(dialect=postgresql.dialect()))
    assert "name =" in items_sql
    assert "id >=" in items_sql
    assert "name =" in count_sql
    assert "id >=" in count_sql


@pytest.mark.asyncio
async def test_base_repository_get_paginated_list_applies_date_range_to_items_and_count() -> (
    None
):
    repo = SearchableRepository()
    session = RepositorySession()
    session.execute.side_effect = [
        FakeResult(items=[]),
        FakeResult(scalar=0),
    ]
    now = get_utc_now()

    await repo.get_paginated_list(
        session=session,
        page=1,
        size=10,
        query=ListQuery(date_from=now - timedelta(days=7), date_to=now),
    )

    items_query, count_query = (
        call.args[0] for call in session.execute.await_args_list
    )
    items_sql = str(items_query.compile(dialect=postgresql.dialect()))
    count_sql = str(count_query.compile(dialect=postgresql.dialect()))
    assert "created_at >=" in items_sql
    assert "created_at <=" in items_sql
    assert "created_at >=" in count_sql
    assert "created_at <=" in count_sql


@pytest.mark.asyncio
async def test_base_repository_get_paginated_list_applies_requested_ordering() -> None:
    repo = SearchableRepository()
    session = RepositorySession()
    session.execute.side_effect = [
        FakeResult(items=[]),
        FakeResult(scalar=0),
    ]

    await repo.get_paginated_list(
        session=session,
        page=1,
        size=10,
        query=ListQuery(order_by="name", order="asc"),
    )

    items_query = session.execute.await_args_list[0].args[0]
    order_by_clauses = [str(clause) for clause in items_query._order_by_clauses]
    assert order_by_clauses == [
        "repository_models.name ASC NULLS LAST",
        "repository_models.id ASC",
    ]


@pytest.mark.asyncio
async def test_base_repository_get_paginated_list_rejects_unlisted_order_field() -> (
    None
):
    repo = SearchableRepository()
    session = RepositorySession()

    with pytest.raises(FilteringError):
        await repo.get_paginated_list(
            session=session, page=1, size=10, query=ListQuery(order_by="deleted_at")
        )

    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_base_repository_get_paginated_list_does_not_eager_load_count_query() -> (
    None
):
    repo = SearchableRepository()
    session = RepositorySession()
    session.execute.side_effect = [
        FakeResult(items=[]),
        FakeResult(scalar=0),
    ]

    await repo.get_paginated_list(
        session=session, page=1, size=10, eager=(load_only(RepositoryModel.name),)
    )

    items_query, count_query = (
        call.args[0] for call in session.execute.await_args_list
    )
    assert items_query._with_options != ()
    assert count_query._with_options == ()


@pytest.mark.asyncio
async def test_soft_delete_repository_get_paginated_list_keeps_is_deleted_filter() -> (
    None
):
    repo = RepositorySoftDeleteRepository()
    session = RepositorySession()
    session.execute.side_effect = [
        FakeResult(items=[]),
        FakeResult(scalar=0),
    ]

    await repo.get_paginated_list(
        session=session,
        page=1,
        size=10,
        query=ListQuery(conditions=FilterCondition(gte={"id": 5})),
    )

    items_query, count_query = (
        call.args[0] for call in session.execute.await_args_list
    )
    items_sql = str(items_query.compile(dialect=postgresql.dialect()))
    count_sql = str(count_query.compile(dialect=postgresql.dialect()))
    assert "is_deleted =" in items_sql
    assert "id >=" in items_sql
    assert "is_deleted =" in count_sql
    assert "id >=" in count_sql


@pytest.mark.asyncio
async def test_base_repository_count_returns_int() -> None:
    repo = RepositoryModelRepository()
    session = RepositorySession()
    session.execute.return_value = FakeResult(scalar=3)

    result = await repo.count(session=session, name="alpha")

    assert result == 3


@pytest.mark.asyncio
async def test_base_repository_update_requires_filters() -> None:
    repo = RepositoryModelRepository()
    session = RepositorySession()

    with pytest.raises(ValueError):
        await repo.update(session=session, data={"name": "new"})


@pytest.mark.asyncio
async def test_base_repository_update_updates_instance_and_commits() -> None:
    repo = RepositoryModelRepository()
    session = RepositorySession()
    instance = RepositoryModel(name="old")
    session.execute.return_value = FakeResult(items=[instance])

    result = await repo.update(
        session=session,
        data={"name": "new"},
        commit=True,
        id=1,
    )

    assert result is instance
    assert instance.name == "new"
    session.commit.assert_awaited_once()
    session.refresh.assert_awaited_once_with(instance)


@pytest.mark.asyncio
async def test_base_repository_update_returns_none_when_not_found() -> None:
    repo = RepositoryModelRepository()
    session = RepositorySession()
    session.execute.return_value = FakeResult(items=[])

    result = await repo.update(
        session=session, data={"name": "new"}, commit=False, id=1
    )

    assert result is None
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_base_repository_delete_requires_filters() -> None:
    repo = RepositoryModelRepository()
    session = RepositorySession()

    with pytest.raises(ValueError):
        await repo.delete(session=session)


@pytest.mark.asyncio
async def test_base_repository_delete_commits_and_returns_instance() -> None:
    repo = RepositoryModelRepository()
    session = RepositorySession()
    instance = RepositoryModel(name="alpha")
    session.execute.return_value = FakeResult(items=[instance])

    result = await repo.delete(session=session, commit=True, id=1)

    assert result is instance
    session.delete.assert_awaited_once_with(instance)
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_base_repository_delete_returns_none_when_not_found() -> None:
    repo = RepositoryModelRepository()
    session = RepositorySession()
    session.execute.return_value = FakeResult(items=[])

    result = await repo.delete(session=session, commit=False, id=1)

    assert result is None
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_repository_xact_lock_uses_namespaced_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = RepositoryModelRepository()
    session = RepositorySession()
    lock_mock = AsyncMock()
    monkeypatch.setattr(
        "src.core.database.repositories.advisory_xact_lock",
        lock_mock,
    )

    await repo.xact_lock(session=session, key="abc")

    lock_mock.assert_awaited_once_with(session, "repository_models:abc")


@pytest.mark.asyncio
async def test_repository_try_xact_lock_uses_namespaced_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = RepositoryModelRepository()
    session = RepositorySession()
    lock_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "src.core.database.repositories.try_advisory_xact_lock",
        lock_mock,
    )

    result = await repo.try_xact_lock(session=session, key="abc")

    assert result is True
    lock_mock.assert_awaited_once_with(session, "repository_models:abc")


def test_soft_delete_repository_requires_fields() -> None:
    with pytest.raises(TypeError):
        NoSoftDeleteRepository()


@pytest.mark.asyncio
async def test_soft_delete_repository_update_requires_filters() -> None:
    repo = RepositorySoftDeleteRepository()
    session = RepositorySession()

    with pytest.raises(ValueError):
        await repo.update(session=session, data={"name": "new"})

    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_soft_delete_repository_delete_requires_filters() -> None:
    repo = RepositorySoftDeleteRepository()
    session = RepositorySession()

    with pytest.raises(ValueError):
        await repo.delete(session=session)

    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_soft_delete_repository_marks_instance_and_commits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = RepositorySoftDeleteRepository()
    session = RepositorySession()
    instance = RepositoryModel(name="alpha")
    session.execute.return_value = FakeResult(items=[instance])
    monkeypatch.setattr(
        "src.core.database.repositories.get_utc_now",
        fixed_utc_now,
    )

    result = await repo.delete(session=session, commit=True, id=1)

    assert result is instance
    assert instance.is_deleted is True
    assert instance.deleted_at == FIXED_NOW
    session.commit.assert_awaited_once()
    session.refresh.assert_awaited_once_with(instance)


@pytest.mark.asyncio
async def test_soft_delete_repository_batch_soft_delete_returns_rowcount(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = RepositorySoftDeleteRepository()
    session = RepositorySession()
    session.execute.return_value = FakeExecuteResult(rowcount=2)
    monkeypatch.setattr(
        "src.core.database.repositories.get_utc_now",
        fixed_utc_now,
    )

    result = await repo.batch_soft_delete(
        session=session, filters=FilterCondition(eq={"id": 1})
    )

    assert result == 2


@pytest.mark.asyncio
async def test_soft_delete_repository_batch_soft_delete_requires_filters_async() -> (
    None
):
    repo = RepositorySoftDeleteRepository()
    session = RepositorySession()

    with pytest.raises(ValueError):
        await repo.batch_soft_delete(session=session, filters=FilterCondition())


@pytest.mark.asyncio
async def test_soft_delete_repository_batch_soft_delete_validates_filters_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = RepositorySoftDeleteRepository()
    session = RepositorySession()
    session.execute.return_value = FakeExecuteResult(rowcount=1)
    validate_calls = 0
    original_validate = FilterCondition.validate

    def counting_validate(self: FilterCondition) -> None:
        nonlocal validate_calls
        validate_calls += 1
        original_validate(self)

    monkeypatch.setattr(FilterCondition, "validate", counting_validate)

    result = await repo.batch_soft_delete(
        session=session,
        filters=FilterCondition(eq={"id": 1}),
    )

    assert result == 1
    assert validate_calls == 1


class ListingModel(SQLAlchemyBase):
    __tablename__ = "listing_models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    quantity: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ListingStatus(enum.StrEnum):
    DRAFT = "draft"


class EnumModel(SQLAlchemyBase):
    __tablename__ = "listing_enum_models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[ListingStatus] = mapped_column(SAEnum(ListingStatus))


class HybridModel(SQLAlchemyBase):
    __tablename__ = "listing_hybrid_models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    first: Mapped[str] = mapped_column(String(64))
    last: Mapped[str] = mapped_column(String(64))

    @hybrid_property
    def full_name(self) -> str:
        return f"{self.first} {self.last}"

    @full_name.expression
    def full_name(cls) -> Any:  # noqa: N805
        return cls.first + " " + cls.last


class RelationshipParentModel(SQLAlchemyBase):
    __tablename__ = "listing_relationship_parents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    children: Mapped[list[RelationshipChildModel]] = relationship(
        back_populates="parent"
    )


class RelationshipChildModel(SQLAlchemyBase):
    __tablename__ = "listing_relationship_children"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    parent_id: Mapped[int] = mapped_column(
        ForeignKey("listing_relationship_parents.id")
    )
    parent: Mapped[RelationshipParentModel] = relationship(back_populates="children")


def test_base_repository_accepts_valid_list_query_fields() -> None:
    class ValidRepository(BaseRepository[ListingModel]):
        model = ListingModel
        searchable_fields = ("name",)
        sortable_fields = ("created_at", "name")
        default_order_by = "created_at"

    assert ValidRepository().searchable_fields == ("name",)


def test_base_repository_rejects_unknown_sortable_field() -> None:
    class BrokenRepository(BaseRepository[ListingModel]):
        model = ListingModel
        sortable_fields = ("published_at",)

    with pytest.raises(TypeError):
        BrokenRepository()


def test_base_repository_rejects_non_column_sortable_field() -> None:
    class BrokenRepository(BaseRepository[ListingModel]):
        model = ListingModel
        sortable_fields = ("__tablename__",)

    with pytest.raises(TypeError):
        BrokenRepository()


def test_base_repository_rejects_unknown_default_order_by() -> None:
    class BrokenRepository(BaseRepository[ListingModel]):
        model = ListingModel
        default_order_by = "published_at"

    with pytest.raises(TypeError):
        BrokenRepository()


def test_base_repository_rejects_unknown_searchable_field() -> None:
    class BrokenRepository(BaseRepository[ListingModel]):
        model = ListingModel
        searchable_fields = ("nickname",)

    with pytest.raises(TypeError):
        BrokenRepository()


def test_base_repository_rejects_non_column_searchable_field() -> None:
    class BrokenRepository(BaseRepository[ListingModel]):
        model = ListingModel
        searchable_fields = ("__tablename__",)

    with pytest.raises(TypeError):
        BrokenRepository()


def test_base_repository_rejects_non_string_searchable_field() -> None:
    class BrokenRepository(BaseRepository[ListingModel]):
        model = ListingModel
        searchable_fields = ("quantity",)

    with pytest.raises(TypeError):
        BrokenRepository()


def test_base_repository_rejects_enum_searchable_field() -> None:
    class BrokenRepository(BaseRepository[EnumModel]):
        model = EnumModel
        searchable_fields = ("status",)

    with pytest.raises(TypeError):
        BrokenRepository()


def test_base_repository_rejects_hybrid_property_sortable_field() -> None:
    # A hybrid property passes search validation (it compiles to a usable
    # ILIKE expression) but is not column-shaped, so it must not pass the
    # orderable check: `build_order_by` cannot dedupe it against the primary
    # key and `.desc()`/`.asc()` on its expression is not what the caller
    # expects.
    class BrokenRepository(BaseRepository[HybridModel]):
        model = HybridModel
        sortable_fields = ("full_name",)

    with pytest.raises(TypeError):
        BrokenRepository()


def test_base_repository_rejects_relationship_sortable_field() -> None:
    class BrokenRepository(BaseRepository[RelationshipParentModel]):
        model = RelationshipParentModel
        sortable_fields = ("children",)

    with pytest.raises(TypeError):
        BrokenRepository()
