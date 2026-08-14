from collections.abc import Sequence
from datetime import datetime
from math import ceil
from typing import Any, Generic, TypeVar, overload

from pydantic import Field, field_validator

from src.core.database.filters import FilterCondition
from src.core.database.query import ListQuery, SortOrder
from src.core.schemas import Base

T = TypeVar("T")
SchemaT = TypeVar("SchemaT", bound=Base)
ItemT = TypeVar("ItemT")


class PaginationParams(Base):
    """Pagination request parameters.

    - page: page number starting from 1 (default: 1)
    - size: page size from 1 to 100 (default: 50)
    """

    page: int = Field(default=1, ge=1)
    size: int = Field(default=50, ge=1, le=100)


class ListQueryParams(PaginationParams):
    """Query parameters for list endpoints.

    - search: case-insensitive substring, matched against the fields the
      resource allows searching by
    - order_by: field to sort by; the resource decides which fields are allowed
    - order: sort direction, "asc" or "desc" (default: "desc")
    - date_from / date_to: inclusive bounds of the period to select
    """

    search: str | None = Field(default=None, max_length=100)
    order_by: str | None = None
    order: SortOrder = "desc"
    date_from: datetime | None = None
    date_to: datetime | None = None

    @field_validator("search", "order_by", mode="after")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        """Normalise a blank/whitespace-only value to `None`.

        `ListQuery` treats `None` as "use the default" for both fields; a
        client that serialises its whole filter form on every request emits
        `search=` and `order_by=` when nothing is selected, and those must
        not reach `ListQuery` as literal empty strings (`order_by=""` fails
        the sortable-fields allowlist and 400s).
        """
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    def to_list_query(
        self,
        conditions: FilterCondition | None = None,
        date_field: str = "created_at",
    ) -> ListQuery:
        """Translate HTTP parameters into the repository-level specification."""
        return ListQuery(
            search=self.search,
            date_from=self.date_from,
            date_to=self.date_to,
            date_field=date_field,
            order_by=self.order_by,
            order=self.order,
            conditions=conditions,
        )


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
