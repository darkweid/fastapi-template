from __future__ import annotations

from typing import Any, Generic, TypeVar, cast

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Load

from src.core.database.base import Base as SQLAlchemyBase
from src.core.database.query import ListQuery
from src.core.database.repositories import BaseRepository
from src.core.errors.exceptions import InstanceNotFoundException
from src.core.pagination import (
    PaginatedResponse,
    PaginationParams,
    make_paginated_response,
)
from src.core.schemas import Base as PydanticBase

ModelType = TypeVar("ModelType", bound=SQLAlchemyBase)
CreateSchema = TypeVar("CreateSchema", bound=PydanticBase)
RepositoryType = TypeVar("RepositoryType", bound=BaseRepository[Any])
ResponseSchema = TypeVar("ResponseSchema", bound=PydanticBase)


class BaseService(Generic[ModelType, CreateSchema, RepositoryType, ResponseSchema]):
    """
    Lightweight generic service that wraps a repository to perform straightforward CRUD operations.

    Use this service only for simple, stateless cases without complicated custom business logic, cross-aggregate
    coordination, or multi-step workflows. Write methods (create/update/delete) commit automatically,
    so changes are persisted immediately.

    The session is constructor-injected by the DI provider that builds the service (never passed
    in from a router or a call site), and every CRUD method forwards it to the repository as-is.

    For any non-trivial flows—transactional orchestration across multiple repositories, conditional
    workflows, side effects (e.g., sending emails, cache updates, external API calls), or retries—
    prefer the Unit of Work (UoW) pattern and dedicated use cases.
    """

    def __init__(
        self,
        repository: RepositoryType,
        session: AsyncSession,
        response_schema: type[ResponseSchema] | None = None,
    ):
        self.repository = repository
        self.session = session
        self._response_schema = response_schema

    async def create(
        self,
        data: CreateSchema,
    ) -> ModelType:
        """Create a new record."""
        result = await self.repository.create(
            session=self.session, data=data.model_dump(), commit=True
        )
        return cast(ModelType, result)

    async def get_single(
        self,
        eager: list[Load] | None = None,
        **filters: Any,
    ) -> ModelType | None:
        """Retrieve a single record matching the filters."""
        return await self.repository.get_single(
            session=self.session, eager=eager, **filters
        )

    async def get_single_or_404(
        self, eager: list[Load] | None = None, **filters: Any
    ) -> ModelType:
        """Retrieve a single record matching the filters or raise a 404 error."""
        obj = await self.repository.get_single(
            session=self.session, eager=eager, **filters
        )
        if obj is None:
            raise InstanceNotFoundException(
                f"{self.repository.model.__name__} not found"
            )
        return cast(ModelType, obj)

    async def get_list(
        self,
        eager: list[Load] | None = None,
        **filters: Any,
    ) -> list[ModelType]:
        """Retrieve a list of records matching the filters."""
        return await self.repository.get_list(
            session=self.session, eager=eager, **filters
        )

    async def get_paginated_list(
        self,
        pagination: PaginationParams,
        eager: list[Load] | None = None,
        query: ListQuery | None = None,
        **filters: Any,
    ) -> PaginatedResponse[ResponseSchema]:
        """Retrieve a paginated list of records matching the filters."""
        items, total = await self.repository.get_paginated_list(
            session=self.session,
            page=pagination.page,
            size=pagination.size,
            eager=eager,
            query=query,
            **filters,
        )
        schema_to_use: type[ResponseSchema] | None = self._response_schema
        if schema_to_use is None:
            raise ValueError("response_schema must be provided for paginated responses")

        return make_paginated_response(
            items=items,
            total=total,
            pagination=pagination,
            schema=schema_to_use,
        )

    async def update(
        self,
        data: PydanticBase,
        **filters: Any,
    ) -> ModelType | None:
        """Update a record matching the filters."""
        return await self.repository.update(
            session=self.session,
            data=data.model_dump(exclude_unset=True),
            **filters,
            commit=True,
        )

    async def delete(self, **filters: Any) -> ModelType | None:
        """Delete a record matching the filters."""
        return await self.repository.delete(
            session=self.session, **filters, commit=True
        )
