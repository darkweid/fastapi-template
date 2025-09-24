from typing import Any, Generic, TypeVar, cast

from fastapi_pagination import Page
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Load

from src.core.database.base import Base as SQLAlchemyBase
from src.core.database.repositories import BaseRepository
from src.core.errors.exceptions import InstanceNotFoundException
from src.core.schemas import Base as PydanticBase

T = TypeVar("T", bound=SQLAlchemyBase)
CreateSchema = TypeVar("CreateSchema", bound=PydanticBase)
UpdateSchema = TypeVar("UpdateSchema", bound=PydanticBase)
RepoType = TypeVar("RepoType", bound=BaseRepository)  # type: ignore


class BaseService(Generic[T, CreateSchema, UpdateSchema, RepoType]):
    """
    Lightweight generic service that wraps a repository to perform straightforward CRUD operations.

    Use this service only for simple, stateless cases without complicated custom business logic, cross-aggregate
    coordination, or multi-step workflows. Write methods (create/update/delete) commit automatically,
    so changes are persisted immediately.

    For any non-trivial flows—transactional orchestration across multiple repositories, conditional
    workflows, side effects (e.g., sending emails, cache updates, external API calls), or retries—
    prefer the Unit of Work (UoW) pattern and dedicated use cases.
    """

    def __init__(self, repository: RepoType):
        self.repository = repository

    async def create(
        self,
        session: AsyncSession,
        data: CreateSchema,
    ) -> T:
        """Create a new record."""
        result = await self.repository.create(
            session=session, data=data.model_dump(), commit=True
        )
        return cast(T, result)

    async def get_single(
        self,
        session: AsyncSession,
        eager: list[Load] | None = None,
        **filters: Any,
    ) -> T | None:
        """Retrieve a single record matching the filters."""
        return await self.repository.get_single(session=session, eager=eager, **filters)

    async def get_single_or_404(
        self, session: AsyncSession, eager: list[Load] | None = None, **filters: Any
    ) -> T:
        """Retrieve a single record matching the filters or raise a 404 error."""
        obj = await self.repository.get_single(session=session, eager=eager, **filters)
        if obj is None:
            raise InstanceNotFoundException(
                f"{self.repository.model.__name__} not found"
            )
        return cast(T, obj)

    async def get_list(
        self,
        session: AsyncSession,
        eager: list[Load] | None = None,
        **filters: Any,
    ) -> list[T]:
        """Retrieve a list of records matching the filters."""
        return await self.repository.get_list(session=session, eager=eager, **filters)

    async def get_paginated_list(
        self,
        session: AsyncSession,
        eager: list[Load] | None = None,
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
            commit=True,
        )

    async def delete(self, session: AsyncSession, **filters: Any) -> T | None:
        """Delete a record matching the filters."""
        return await self.repository.delete(session=session, **filters, commit=True)
