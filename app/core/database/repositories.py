from typing import Type, TypeVar, Optional

from fastapi_pagination import Page
from fastapi_pagination.ext.async_sqlalchemy import paginate
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from loggers import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


class BaseRepository:
    """Base repository with common SQLAlchemy operations using context-managed sessions."""

    def __init__(self, model: Type[T]):
        self.model = model

    async def create(self, data: dict, session: AsyncSession) -> T:
        """Create a new record using the provided session."""
        try:
            instance = self.model(**data)
            session.add(instance)
            await session.commit()
            await session.refresh(instance)
            logger.info("%s created successfully.", self.model.__name__)
            return instance
        except (IntegrityError, SQLAlchemyError):
            await session.rollback()
            raise

    async def get_single(self, session: AsyncSession, **filters) -> Optional[T]:
        """Retrieve a single record using the provided session."""
        query = select(self.model).filter_by(**filters)
        result = await session.execute(query)
        return result.scalars().first()

    async def get_list(self, session: AsyncSession, **filters) -> Page[T]:
        """Retrieve a paginated list of records using the provided session."""
        query = select(self.model).filter_by(**filters).order_by(self.model.created_at.desc())
        return await paginate(session, query)

    async def update(self, data: dict, session: AsyncSession, **filters) -> Optional[T]:
        """Update a record using the provided session."""
        try:
            query = select(self.model).filter_by(**filters)
            result = await session.execute(query)
            instance = result.scalars().first()
            if instance:
                for key, value in data.items():
                    setattr(instance, key, value)
                await session.commit()
                await session.refresh(instance)
                logger.info("%s updated successfully.", self.model.__name__)
                return instance
            return None
        except (IntegrityError, SQLAlchemyError):
            await session.rollback()
            raise

    async def delete(self, session: AsyncSession, **filters) -> Optional[T]:
        """Delete a record using the provided session."""
        try:
            query = select(self.model).filter_by(**filters)
            result = await session.execute(query)
            instance = result.scalars().first()
            if instance:
                await session.delete(instance)
                await session.commit()
                logger.info("%s deleted successfully.", self.model.__name__)
                return instance
            return None
        except (IntegrityError, SQLAlchemyError):
            await session.rollback()
            raise


class SoftDeleteRepository(BaseRepository):
    """Repository with soft delete support."""

    async def get_single(self, session: AsyncSession, **filters) -> Optional[T]:
        """Retrieve a single record where is_deleted flag is False, using the provided session and filters."""
        filters.setdefault("is_deleted", False)
        return await super().get_single(session, **filters)

    async def get_list(self, session: AsyncSession, **filters) -> Page[T]:
        """Retrieve a list of records where is_deleted flag is False, using the provided session and filters."""
        filters.setdefault("is_deleted", False)
        return await super().get_list(session, **filters)

    async def update(self, data: dict, session: AsyncSession, **filters) -> Optional[T]:
        """Update a record where is_deleted flag is False, using the provided session and filters."""
        filters.setdefault("is_deleted", False)
        return await super().update(data, session, **filters)

    async def delete(self, session: AsyncSession, **filters) -> Optional[T]:
        """Soft delete a record, using the provided session and filters."""
        filters.setdefault("is_deleted", False)
        try:
            query = select(self.model).filter_by(**filters)
            result = await session.execute(query)
            instance = result.scalars().first()
            if instance:
                instance.is_deleted = True
                await session.commit()
                await session.refresh(instance)
                logger.info("%s soft deleted successfully.", self.model.__name__)
                return instance
            return None
        except (IntegrityError, SQLAlchemyError):
            await session.rollback()
            raise


class LastEntryRepository:
    """
    Repository for CRUD operations with a focus on retrieving the latest entry.

    This repository provides methods to create a new record and to retrieve the most recent
    record based on the 'created_at' attribute of the model.
    """

    def __init__(self, model: Type[T]):
        """
        Initialize the repository with the specified SQLAlchemy model.
        """
        self.model = model

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
