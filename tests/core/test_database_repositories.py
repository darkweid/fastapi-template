from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import Boolean, DateTime, Integer, String
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database.base import Base as SQLAlchemyBase
from src.core.database.repositories import (
    BaseRepository,
    LastEntryRepository,
    SoftDeleteRepository,
)


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


class RepositoryLastEntryRepository(LastEntryRepository[RepositoryModel]):
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
async def test_base_repository_get_list_returns_all() -> None:
    repo = RepositoryModelRepository()
    session = RepositorySession()
    items = [RepositoryModel(name="alpha"), RepositoryModel(name="beta")]
    session.execute.return_value = FakeResult(items=items)

    result = await repo.get_list(session=session)

    assert result == items


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

    result = await repo.batch_soft_delete(session=session, id=1)

    assert result == 2


@pytest.mark.asyncio
async def test_soft_delete_repository_batch_soft_delete_requires_filters_async() -> (
    None
):
    repo = RepositorySoftDeleteRepository()
    session = RepositorySession()

    with pytest.raises(ValueError):
        await repo.batch_soft_delete(session=session)


@pytest.mark.asyncio
async def test_last_entry_repository_create_commits() -> None:
    repo = RepositoryLastEntryRepository()
    session = RepositorySession()

    instance = await repo.create(
        data={"name": "alpha"},
        session=session,
    )

    assert isinstance(instance, RepositoryModel)
    session.commit.assert_awaited_once()
    session.refresh.assert_awaited_once_with(instance)


@pytest.mark.asyncio
async def test_last_entry_repository_create_rolls_back_on_integrity_error() -> None:
    repo = RepositoryLastEntryRepository()
    session = RepositorySession()
    session.commit.side_effect = IntegrityError("stmt", "params", "orig")

    with pytest.raises(IntegrityError):
        await repo.create(data={"name": "alpha"}, session=session)

    session.rollback.assert_awaited_once()


@pytest.mark.asyncio
async def test_last_entry_repository_get_single_returns_first() -> None:
    repo = RepositoryLastEntryRepository()
    session = RepositorySession()
    first = RepositoryModel(name="alpha")
    second = RepositoryModel(name="beta")
    session.execute.return_value = FakeResult(items=[first, second])

    result = await repo.get_single(session=session)

    assert result is first
