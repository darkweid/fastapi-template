from collections.abc import Sequence
from datetime import datetime
from typing import Any, Generic, TypeVar, cast

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from loggers import get_logger
from src.core.database.base import Base as SQLAlchemyBase
from src.core.database.types import EagerLoadSequence
from src.core.utils.datetime_utils import get_utc_now

logger = get_logger(__name__)

T = TypeVar("T", bound=SQLAlchemyBase)


class BaseRepository(Generic[T]):
    """Base repository with common SQLAlchemy operations using context-managed sessions."""

    model: type[T]

    def __init__(self) -> None:
        if not hasattr(self, "model"):
            raise NotImplementedError("Subclasses must define class variable 'model'")

    async def create(
        self, session: AsyncSession, data: dict[str, Any], commit: bool = False
    ) -> T:
        """Create a new record using the provided session."""
        try:
            instance = self.model(**data)
            session.add(instance)
            if commit:
                await session.commit()
                await session.refresh(instance)
                logger.info("%s created successfully [Committed].", self.model.__name__)
            else:
                logger.debug(
                    "%s created [Staged, pending commit].", self.model.__name__
                )
            return instance
        except (IntegrityError, SQLAlchemyError):
            if commit:
                await session.rollback()
            raise

    async def exists(
        self, session: AsyncSession, strict_single: bool = False, **filters: Any
    ) -> bool:
        """
        Determine if a record exists in the database matching the provided filters.
        Optionally, it can enforce strict single-record existence checks.
        """
        if strict_single:
            query = select(1).select_from(self.model).filter_by(**filters).limit(2)
            rows = (await session.execute(query)).all()
            return len(rows) == 1
        else:
            subquery = select(1).select_from(self.model).filter_by(**filters).limit(1)
            query = select(subquery.exists())
            return bool(await session.scalar(query))

    async def get_single(
        self,
        session: AsyncSession,
        eager: EagerLoadSequence | None = None,
        for_update: bool = False,
        **filters: Any,
    ) -> T | None:
        """Retrieve a single record using the provided session."""
        query = select(self.model).filter_by(**filters).limit(1)

        if eager:
            query = query.options(*eager)
        if for_update:
            # Limit lock scope to this table to avoid Postgres outer-join restriction
            table = getattr(self.model, "__table__")
            pk_columns = tuple(
                cast("ColumnElement[Any]", column)
                for column in table.primary_key.columns
            )
            if pk_columns:
                query = query.with_for_update(of=pk_columns)
            else:
                query = query.with_for_update(of=(table,))

        result = await session.execute(query)
        return result.unique().scalars().first()

    async def get_list(
        self,
        session: AsyncSession,
        eager: EagerLoadSequence | None = None,
        for_update: bool = False,
        **filters: Any,
    ) -> list[T]:
        """Retrieve a list of records using the provided session without pagination."""
        query = select(self.model).filter_by(**filters)
        if eager:
            query = query.options(*eager)
        if for_update:
            # Limit lock scope to this table to avoid Postgres outer-join restriction
            table = getattr(self.model, "__table__")
            pk_columns = tuple(
                cast("ColumnElement[Any]", column)
                for column in table.primary_key.columns
            )
            if pk_columns:
                query = query.with_for_update(of=pk_columns)
            else:
                query = query.with_for_update(of=(table,))

        order_by = getattr(self.model, "created_at", None)
        if order_by is None:
            order_by = getattr(self.model, "id", None)
        if order_by is not None:
            query = query.order_by(order_by.desc())

        result = await session.execute(query)
        return list(result.unique().scalars().all())

    async def get_paginated_list(
        self,
        session: AsyncSession,
        page: int,
        size: int,
        eager: EagerLoadSequence | None = None,
        **filters: Any,
    ) -> tuple[list[T], int]:
        """Retrieve a paginated list of records using limit/offset pagination."""
        if page < 1:
            raise ValueError("page must be greater than or equal to 1")
        if size < 1:
            raise ValueError("size must be greater than or equal to 1")

        query = select(self.model).filter_by(**filters)
        if eager:
            query = query.options(*eager)

        order_by = getattr(self.model, "created_at", None)
        if order_by is None:
            order_by = getattr(self.model, "id", None)
        if order_by is not None:
            query = query.order_by(order_by.desc())

        offset = (page - 1) * size
        query = query.offset(offset).limit(size)

        result = await session.execute(query)
        items = list(result.unique().scalars().all())

        count_query = select(func.count()).select_from(self.model).filter_by(**filters)
        total_result = await session.execute(count_query)
        total = int(total_result.scalar_one())

        return items, total

    async def count(
        self,
        session: AsyncSession,
        **filters: Any,
    ) -> int:
        """Count records matching the provided filters using the given session."""
        query = select(func.count()).select_from(self.model).filter_by(**filters)
        result = await session.execute(query)
        count_value = result.scalar_one()
        return int(count_value)

    async def update(
        self,
        session: AsyncSession,
        data: dict[str, Any],
        commit: bool = False,
        **filters: Any,
    ) -> T | None:
        """Update a record using the provided session."""
        self._ensure_filters_present(filters)
        try:
            query = select(self.model).filter_by(**filters)
            result = await session.execute(query)
            instance = result.scalars().first()
            if instance:
                for key, value in data.items():
                    setattr(instance, key, value)
                if commit:
                    await session.commit()
                    await session.refresh(instance)
                    logger.info(
                        "%s updated successfully [Committed].", self.model.__name__
                    )
                else:
                    logger.debug(
                        "%s updated [Staged, pending commit].", self.model.__name__
                    )
                return instance

            logger.debug(
                "%s update skipped [NotFound]. filters=%s", self.model.__name__, filters
            )
            return None
        except (IntegrityError, SQLAlchemyError):
            if commit:
                await session.rollback()
            raise

    async def delete(
        self, session: AsyncSession, commit: bool = False, **filters: Any
    ) -> T | None:
        """Delete a record using the provided session."""
        self._ensure_filters_present(filters)
        try:
            query = select(self.model).filter_by(**filters)
            result = await session.execute(query)
            instance = result.scalars().first()
            if instance:
                await session.delete(instance)
                if commit:
                    await session.commit()
                    logger.info(
                        "%s deleted successfully [Committed].", self.model.__name__
                    )
                else:
                    logger.debug(
                        "%s deleted [Staged, pending commit].", self.model.__name__
                    )
                return instance
            return None
        except (IntegrityError, SQLAlchemyError):
            if commit:
                await session.rollback()
            raise

    def _apply_search_filter(
        self,
        query: Any,
        search: str | None = None,
        fields: Sequence[str | Any] | None = None,
    ) -> Any:
        if not search or not fields:
            return query

        search_query_list = []

        for field in fields:
            if isinstance(field, str) and hasattr(self.model, field):
                search_query_list.append(
                    getattr(self.model, field).ilike(f"%{search}%")
                )
            elif hasattr(field, "ilike"):
                search_query_list.append(field.ilike(f"%{search}%"))

        if search_query_list:
            query = query.where(or_(*search_query_list))

        return query

    def _apply_date_filter(
        self,
        query: Any,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        field: str = "created_at",
    ) -> Any:
        """
        Apply an inclusive date filter on a datetime column.
        - If only from_date is provided: col >= from_date
        - If only to_date is provided:   col <= to_date
        - If both are provided:          from_date <= col <= to_date
        Notes:
          * If from_date > to_date, the bounds are swapped.
          * If the model does not have `field`, the query is returned unchanged.
        """
        if not hasattr(self.model, field):
            return query

        if from_date is None and to_date is None:
            return query

        col = getattr(self.model, field)

        if from_date is not None and to_date is not None:
            if from_date > to_date:
                from_date, to_date = to_date, from_date
            return query.where(col >= from_date).where(col <= to_date)

        if from_date is not None:
            return query.where(col >= from_date)

        # only to_date is not None
        return query.where(col <= to_date)

    @staticmethod
    def _ensure_filters_present(filters: dict[str, Any]) -> None:
        if not filters:
            raise ValueError("At least one filter must be provided for update/delete")


class SoftDeleteRepository(BaseRepository[T], Generic[T]):
    """Repository with soft delete support."""

    def __init__(self) -> None:
        super().__init__()
        self._assert_softdelete_fields()

    async def exists(
        self, session: AsyncSession, strict_single: bool = False, **filters: Any
    ) -> bool:
        filters.setdefault("is_deleted", False)
        return await super().exists(session, strict_single=strict_single, **filters)

    async def get_single(
        self,
        session: AsyncSession,
        eager: EagerLoadSequence | None = None,
        for_update: bool = False,
        **filters: Any,
    ) -> T | None:
        """Retrieve a single record where the is_deleted flag is False, using the provided session and filters."""
        filters.setdefault("is_deleted", False)
        return await super().get_single(
            session, eager=eager, for_update=for_update, **filters
        )

    async def get_list(
        self,
        session: AsyncSession,
        eager: EagerLoadSequence | None = None,
        for_update: bool = False,
        **filters: Any,
    ) -> list[T]:
        """Retrieve a list of records where the is_deleted flag is False, using the provided session and filters."""
        filters.setdefault("is_deleted", False)
        return await super().get_list(
            session, eager=eager, for_update=for_update, **filters
        )

    async def get_paginated_list(
        self,
        session: AsyncSession,
        page: int,
        size: int,
        eager: EagerLoadSequence | None = None,
        **filters: Any,
    ) -> tuple[list[T], int]:
        """Retrieve a list of records where is_deleted flag is False, using the filters,
        with pagination."""
        filters.setdefault("is_deleted", False)
        return await super().get_paginated_list(
            session, page=page, size=size, eager=eager, **filters
        )

    async def count(
        self,
        session: AsyncSession,
        **filters: Any,
    ) -> int:
        """Count records matching the provided filters using the given session."""
        filters.setdefault("is_deleted", False)
        return await super().count(session, **filters)

    async def update(
        self,
        session: AsyncSession,
        data: dict[str, Any],
        commit: bool = False,
        **filters: Any,
    ) -> T | None:
        """Update a record where is_deleted flag is False, using the filters."""
        filters.setdefault("is_deleted", False)
        return await super().update(session, data, commit, **filters)

    async def delete(
        self, session: AsyncSession, commit: bool = False, **filters: Any
    ) -> T | None:
        """Soft delete a record, using the filters."""
        filters.setdefault("is_deleted", False)
        try:
            query = select(self.model).filter_by(**filters)
            result = await session.execute(query)
            instance: T | None = result.scalars().first()
            if instance:
                setattr(instance, "is_deleted", True)
                setattr(instance, "deleted_at", get_utc_now())
                if commit:
                    await session.commit()
                    await session.refresh(instance)
                    logger.info(
                        "%s soft-deleted successfully [Committed].", self.model.__name__
                    )
                else:
                    logger.debug(
                        "%s soft-deleted [Staged, pending commit].", self.model.__name__
                    )
                return instance
            return None
        except (IntegrityError, SQLAlchemyError):
            if commit:
                await session.rollback()
            raise

    def _assert_softdelete_fields(self) -> None:
        if not hasattr(self.model, "is_deleted") or not hasattr(
            self.model, "deleted_at"
        ):
            raise TypeError(
                f"{self.model.__name__} must define 'is_deleted' and 'deleted_at' for SoftDeleteRepository"
            )


class LastEntryRepository(Generic[T]):
    """
    Repository for CRUD operations with a focus on retrieving the latest entry.

    This repository provides methods to create a new record and to retrieve the most recent
    record based on the 'created_at' attribute of the model.
    """

    model: type[T]

    async def create(self, data: dict[str, Any], session: AsyncSession) -> T:
        """
        Create a new record in the database using the provided session.
        """
        try:
            instance = self.model(**data)
            session.add(instance)
            await session.commit()
            await session.refresh(instance)
            logger.info("%s created successfully.", self.model.__name__)
            return instance
        except (IntegrityError, SQLAlchemyError) as e:
            await session.rollback()
            logger.error("Error creating %s: %s", self.model.__name__, e)
            raise

    async def get_single(self, session: AsyncSession) -> T | None:
        """
        Retrieve the most recent record based on the 'created_at' field.
        """
        query = select(self.model)
        if getattr(self.model, "created_at", None):
            query = query.order_by(self.model.created_at.desc())  # type: ignore
        else:
            query = query.order_by(self.model.id.desc())  # type: ignore
        query = query.limit(1)
        result = await session.execute(query)
        return result.scalars().first()
