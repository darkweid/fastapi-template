from __future__ import annotations

import inspect
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database.base import Base as SQLAlchemyBase
from src.core.database.query import ListQuery
from src.core.database.repositories import BaseRepository
from src.core.pagination import PaginationParams
from src.core.schemas import Base as PydanticBase
from src.core.services import BaseService


class ServiceModel(SQLAlchemyBase):
    __tablename__ = "service_listing_models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64))


class ServiceModelView(PydanticBase):
    id: int
    name: str


class ServiceModelRepository(BaseRepository[ServiceModel]):
    model = ServiceModel
    searchable_fields = ("name",)
    sortable_fields = ("name",)


@pytest.mark.asyncio
async def test_base_service_passes_list_query_to_repository() -> None:
    repository = ServiceModelRepository()
    repository.get_paginated_list = AsyncMock(return_value=([], 0))
    service: BaseService[
        ServiceModel, ServiceModelView, ServiceModelRepository, ServiceModelView
    ] = BaseService(
        repository=repository, session=AsyncMock(), response_schema=ServiceModelView
    )
    list_query = ListQuery(search="alpha", order_by="name", order="asc")

    response = await service.get_paginated_list(
        pagination=PaginationParams(page=1, size=10),
        query=list_query,
        is_active=True,
    )

    assert response.total == 0
    assert repository.get_paginated_list.await_args.kwargs["query"] is list_query
    assert repository.get_paginated_list.await_args.kwargs["is_active"] is True
    assert "query" in inspect.signature(BaseService.get_paginated_list).parameters
