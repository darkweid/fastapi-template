from __future__ import annotations

from pydantic import ValidationError
import pytest

from src.core.pagination.schemas import (
    PaginatedResponse,
    PaginationParams,
    make_paginated_response,
)
from src.core.schemas import Base


class ItemSchema(Base):
    id: int
    name: str


def test_pagination_params_validation() -> None:
    PaginationParams(page=1, size=10)

    with pytest.raises(ValidationError):
        PaginationParams(page=0, size=10)

    with pytest.raises(ValidationError):
        PaginationParams(page=1, size=101)


def test_make_paginated_response_with_schema() -> None:
    params = PaginationParams(page=1, size=2)
    items = [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}]

    response = make_paginated_response(
        items=items, total=5, pagination=params, schema=ItemSchema
    )

    assert isinstance(response, PaginatedResponse)
    assert response.total == 5
    assert response.pages == 3
    assert response.items[0].id == 1


def test_make_paginated_response_without_schema() -> None:
    params = PaginationParams(page=2, size=2)
    items = [1, 2]

    response = make_paginated_response(items=items, total=0, pagination=params)

    assert response.items == [1, 2]
    assert response.pages == 0
