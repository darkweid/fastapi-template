from typing import Optional, TypeVar

from fastapi_pagination import Page
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

T = TypeVar("T")
R = TypeVar("R", bound=BaseModel)


class BaseService:
    """Base service with common CRUD operations using Pydantic models."""

    def __init__(self, repository):
        self.repository = repository

    async def create(self, data: R, session: AsyncSession) -> Optional[T]:
        """Create a new record using the provided session."""
        return await self.repository.create(data.model_dump(), session)

    async def get_single(self, session: AsyncSession, **filters) -> Optional[T]:
        """Retrieve a single record matching the filters using the provided session."""
        return await self.repository.get_single(session, **filters)

    async def get_list(self, session: AsyncSession, **filters) -> Page[T]:
        """Retrieve a list of records matching the filters using the provided session."""
        return await self.repository.get_list(session, **filters)

    async def update(self, data: R, session: AsyncSession, **filters) -> Optional[T]:
        """Update a record matching the filters using the provided session."""
        return await self.repository.update(data.model_dump(exclude_unset=True), session, **filters)

    async def delete(self, session: AsyncSession, **filters) -> Optional[T]:
        """Delete a record matching the filters using the provided session."""
        return await self.repository.delete(session, **filters)
