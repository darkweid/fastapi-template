from collections.abc import Sequence
from math import ceil
from typing import Any, Generic, TypeVar, overload

from pydantic import Field

from src.core.schemas import Base

T = TypeVar("T")
SchemaT = TypeVar("SchemaT", bound=Base)
ItemT = TypeVar("ItemT")


class PaginationParams(Base):
    """Pagination request parameters.

    - page: page number starting from 1
    - size: page size from 1 to 100
    """

    page: int = Field(..., ge=1)
    size: int = Field(..., ge=1, le=100)


class PaginatedResponse(Base, Generic[T]):
    """Generic paginated response container."""

    items: list[T]
    total: int
    page: int
    size: int
    pages: int


@overload
def make_paginated_response(
    *,
    items: Sequence[ItemT],
    total: int,
    pagination: PaginationParams,
    schema: None = None,
) -> PaginatedResponse[ItemT]: ...


@overload
def make_paginated_response(
    *,
    items: Sequence[Any],
    total: int,
    pagination: PaginationParams,
    schema: type[SchemaT],
) -> PaginatedResponse[SchemaT]: ...


def make_paginated_response(
    *,
    items: Sequence[Any],
    total: int,
    pagination: PaginationParams,
    schema: type[SchemaT] | None = None,
) -> PaginatedResponse[Any]:
    """Construct a paginated response using total count and request params."""
    pages = ceil(total / pagination.size) if total else 0
    if schema is not None:
        parsed_items = [
            item if isinstance(item, schema) else schema.model_validate(item)
            for item in items
        ]
    else:
        parsed_items = list(items)
    return PaginatedResponse(
        items=parsed_items,
        total=total,
        page=pagination.page,
        size=pagination.size,
        pages=pages,
    )
