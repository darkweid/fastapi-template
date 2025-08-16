from datetime import datetime
from typing import Type, TypeVar, Optional, List, Generic, Any, Sequence, Union

from fastapi_pagination import Page
from fastapi_pagination.ext.sqlalchemy import paginate
from sqlalchemy import select, or_
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.database.models import Base as SQLAlchemyBase
from src.core.utils import get_utc_now
from loggers import get_logger

logger = get_logger(__name__)

T = TypeVar("T", bound=SQLAlchemyBase)


class BaseRepository(Generic[T]):
    """Base repository with common SQLAlchemy operations using context-managed sessions."""

    model: Type[T]

    def __init__(self) -> None:
        if not hasattr(self, "model"):
            raise NotImplementedError("Subclasses must define class variable 'model'")

    async def create(self,
                     session: AsyncSession,
                     data: dict[str, Any],
                     commit: bool = True) -> Optional[T]:
        """Create a new record using the provided session."""
        try:
            instance = self.model(**data)
            session.add(instance)
            if commit:
                await session.commit()
                await session.refresh(instance)
            logger.info("%s created successfully.", self.model.__name__)
            return instance
        except (IntegrityError, SQLAlchemyError):
            if commit:
                await session.rollback()
            raise

    async def get_single(self,
                         session: AsyncSession,
                         **filters: Any) -> Optional[T]:
        """Retrieve a single record using the provided session."""
        query = select(self.model).filter_by(**filters)
        result = await session.execute(query)
        return result.scalars().first()

    async def get_list(self,
                       session: AsyncSession,
                       **filters: Any) -> List[T]:
        """Retrieve a list of records using the provided session without pagination."""
        query = select(self.model).filter_by(**filters)

        order_by = getattr(self.model, "created_at", None)
        if order_by is None:
            order_by = getattr(self.model, "id", None)
        if order_by is not None:
            query = query.order_by(order_by.desc())

        result = await session.execute(query)
        return list(result.scalars().all())

    async def get_paginated_list(self,
                                 session: AsyncSession,
                                 **filters: Any) -> Page[T]:
        """Retrieve a paginated list of records using the provided session."""
        query = select(self.model).filter_by(**filters)

        order_by = getattr(self.model, "created_at", None)
        if order_by is None:
            order_by = getattr(self.model, "id", None)
        if order_by is not None:
            query = query.order_by(order_by.desc())

        return await paginate(session, query)  # type: ignore

    async def update(self,
                     session: AsyncSession,
                     data: dict[str, Any],
                     commit: bool = True,
                     **filters: Any) -> Optional[T]:
        """Update a record using the provided session."""
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
                logger.info("%s updated successfully.", self.model.__name__)
                return instance
            return None
        except (IntegrityError, SQLAlchemyError):
            if commit:
                await session.rollback()
            raise

    async def delete(self,
                     session: AsyncSession,
                     commit: bool = True,
                     **filters: Any) -> Optional[T]:
        """Delete a record using the provided session."""
        try:
            query = select(self.model).filter_by(**filters)
            result = await session.execute(query)
            instance = result.scalars().first()
            if instance:
                await session.delete(instance)
                if commit:
                    await session.commit()
                logger.info("%s deleted successfully.", self.model.__name__)
                return instance
            return None
        except (IntegrityError, SQLAlchemyError):
            if commit:
                await session.rollback()
            raise

    def _apply_search_filter(
            self,
            query: Any,
            search: Optional[str] = None,
            fields: Optional[Sequence[Union[str, Any]]] = None,
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
            from_date: Optional[datetime] = None,
            to_date: Optional[datetime] = None,
            field: str = "created_at",
    ) -> Any:
        if from_date and to_date and hasattr(self.model, field):
            query = query.where(getattr(self.model, field).between(from_date, to_date))
        return query


class SoftDeleteRepository(BaseRepository[T], Generic[T]):
    """Repository with soft delete support."""

    async def get_single(self,
                         session: AsyncSession,
                         **filters: Any) -> Optional[T]:
        """Retrieve a single record where is_deleted flag is False, using the provided session and filters."""
        filters.setdefault("is_deleted", False)
        return await super().get_single(session, **filters)

    async def get_list(self,
                       session: AsyncSession,
                       **filters: Any) -> List[T]:
        """Retrieve a list of records where is_deleted flag is False, using the provided session and filters."""
        filters.setdefault("is_deleted", False)
        return await super().get_list(session, **filters)

    async def get_paginated_list(self,
                                 session: AsyncSession,
                                 **filters: Any) -> Page[T]:
        """Retrieve a list of records where is_deleted flag is False, using the provided session and filters,
        with pagination."""
        filters.setdefault("is_deleted", False)
        return await super().get_paginated_list(session, **filters)

    async def update(self,
                     session: AsyncSession,
                     data: dict[str, Any],
                     commit: bool = True,
                     **filters: Any) -> Optional[T]:
        """Update a record where is_deleted flag is False, using the provided session and filters."""
        filters.setdefault("is_deleted", False)
        return await super().update(session, data, commit, **filters)

    async def delete(self,
                     session: AsyncSession,
                     commit: bool = True,
                     **filters: Any) -> Optional[T]:
        """Soft delete a record, using the provided session and filters."""
        filters.setdefault("is_deleted", False)
        try:
            query = select(self.model).filter_by(**filters)
            result = await session.execute(query)
            instance: Optional[T] = result.scalars().first()
            if instance:
                setattr(instance, "is_deleted", True)
                setattr(instance, "deleted_at", get_utc_now())
                if commit:
                    await session.commit()
                    await session.refresh(instance)
                logger.info("%s soft deleted successfully.", self.model.__name__)
                return instance
            return None
        except (IntegrityError, SQLAlchemyError):
            if commit:
                await session.rollback()
            raise


class LastEntryRepository(Generic[T]):
    """
    Repository for CRUD operations with a focus on retrieving the latest entry.

    This repository provides methods to create a new record and to retrieve the most recent
    record based on the 'created_at' attribute of the model.
    """

    model: Type[T]

    async def create(self, data: dict, session: AsyncSession) -> Optional[T]:
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

    async def get_single(self, session: AsyncSession) -> Optional[T]:
        """
        Retrieve the most recent record based on the 'created_at' field.
        """
        query = select(self.model).order_by(self.model.created_at.desc()).limit(1)
        result = await session.execute(query)
        return result.scalars().first()
