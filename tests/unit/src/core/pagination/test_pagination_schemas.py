from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Any

from fastapi import FastAPI, Query
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from src.core.database.filters import FilterCondition
from src.core.pagination import ListQueryParams
from src.core.pagination.schemas import (
    PaginatedResponse,
    PaginationParams,
    make_paginated_response,
)
from src.core.schemas import Base
from src.core.utils.datetime_utils import get_utc_now


class ItemSchema(Base):
    id: int
    name: str


def test_pagination_params_validation() -> None:
    params = PaginationParams()

    assert params.page == 1
    assert params.size == 50

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


def test_list_query_params_defaults_are_inert() -> None:
    params = ListQueryParams()

    list_query = params.to_list_query()

    assert (params.page, params.size) == (1, 50)
    assert list_query.search is None
    assert list_query.order_by is None
    assert list_query.order == "desc"
    assert list_query.date_field == "created_at"
    assert list_query.conditions is None


def test_list_query_params_transfer_every_value() -> None:
    now = get_utc_now()
    conditions = FilterCondition(gte={"id": 3})
    params = ListQueryParams(
        page=2,
        size=10,
        search="anne",
        order_by="email",
        order="asc",
        date_from=now - timedelta(days=1),
        date_to=now,
    )

    list_query = params.to_list_query(conditions=conditions, date_field="updated_at")

    assert list_query.search == "anne"
    assert list_query.order_by == "email"
    assert list_query.order == "asc"
    assert list_query.date_from == now - timedelta(days=1)
    assert list_query.date_to == now
    assert list_query.date_field == "updated_at"
    assert list_query.conditions is conditions


def test_list_query_params_reject_overlong_search() -> None:
    with pytest.raises(ValidationError):
        ListQueryParams(search="x" * 101)


def test_list_query_params_reject_unknown_order_direction() -> None:
    with pytest.raises(ValidationError):
        ListQueryParams(order="sideways")


def test_list_query_params_normalizes_blank_order_by_to_none() -> None:
    # A front end that serialises its whole filter form emits `order_by=`
    # when nothing is selected; `ListQuery` treats `None` as "use the
    # default", so a blank string must not reach it as a literal `""`.
    assert ListQueryParams(order_by="   ").to_list_query().order_by is None


def test_list_query_params_normalizes_blank_search_to_none() -> None:
    assert ListQueryParams(search="   ").to_list_query().search is None


def test_list_query_params_binds_from_query_string() -> None:
    # This is the documented router pattern (`architecture.md`):
    # `Annotated[ListQueryParams, Query()]`. It is the one integration point
    # where a Pydantic-model-as-query-params regression in a FastAPI bump
    # would land silently, so it is worth pinning with a real `TestClient`
    # instead of only constructing `ListQueryParams` directly.
    app = FastAPI()

    @app.get("/items")
    def list_items(params: Annotated[ListQueryParams, Query()]) -> dict[str, Any]:
        return params.model_dump(mode="json")

    client = TestClient(app)

    response = client.get(
        "/items",
        params={
            "page": 2,
            "size": 10,
            "search": "anne",
            "order_by": "name",
            "order": "asc",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["page"] == 2
    assert body["size"] == 10
    assert body["search"] == "anne"
    assert body["order_by"] == "name"
    assert body["order"] == "asc"


def test_list_query_params_rejects_unknown_query_parameter() -> None:
    app = FastAPI()

    @app.get("/items")
    def list_items(params: Annotated[ListQueryParams, Query()]) -> dict[str, Any]:
        return params.model_dump(mode="json")

    client = TestClient(app)

    response = client.get("/items", params={"utm_source": "newsletter"})

    assert response.status_code == 422
