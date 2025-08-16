from typing import Any, Generic, TypeVar

from fastapi_pagination import Page
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.strategy_options import _AbstractLoad

from src.core.database.models import Base as SQLAlchemyBase
from src.core.database.repositories import BaseRepository
from src.core.schemas import Base as PydanticBase

T = TypeVar("T", bound=SQLAlchemyBase)
CreateSchema = TypeVar("CreateSchema", bound=PydanticBase)
UpdateSchema = TypeVar("UpdateSchema", bound=PydanticBase)
RepoType = TypeVar("RepoType", bound=BaseRepository)  # type: ignore


class BaseService(Generic[T, CreateSchema, UpdateSchema, RepoType]):
    """Base service with common CRUD operations using Pydantic models."""

    def __init__(self, repository: RepoType):
        self.repository = repository

    async def create(
        self,
        session: AsyncSession,
        data: CreateSchema,
    ) -> T:
        """Create a new record."""
        return await self.repository.create(session=session, data=data.model_dump())

    async def get_single(
        self,
        session: AsyncSession,
        eager: list[_AbstractLoad] | None = None,
        **filters: Any,
    ) -> T | None:
        """Retrieve a single record matching the filters."""

        return await self.repository.get_single(session=session, eager=eager, **filters)

    async def get_list(
        self,
        session: AsyncSession,
        eager: list[_AbstractLoad] | None = None,
        **filters: Any,
    ) -> list[T]:
        """Retrieve a list of records matching the filters."""
        return await self.repository.get_list(session=session, eager=eager, **filters)

    async def get_paginated_list(
        self,
        session: AsyncSession,
        eager: list[_AbstractLoad] | None = None,
        **filters: Any,
    ) -> Page[T]:
        """Retrieve a paginated list of records matching the filters."""
        return await self.repository.get_paginated_list(
            session=session, eager=eager, **filters
        )

    async def update(
        self,
        session: AsyncSession,
        data: UpdateSchema,
        **filters: Any,
    ) -> T | None:
        """Update a record matching the filters."""
        return await self.repository.update(
            session=session,
            data=data.model_dump(exclude_unset=True),
            **filters,
        )

    async def delete(self, session: AsyncSession, **filters: Any) -> T | None:
        """Delete a record matching the filters."""
        return await self.repository.delete(session=session, **filters)
