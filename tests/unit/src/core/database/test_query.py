from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import Boolean, DateTime, Integer, String, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.compiler import Compiled
from sqlalchemy.sql.elements import ColumnElement

from src.core.database.base import Base as SQLAlchemyBase
from src.core.database.filters import FilterCondition
from src.core.database.query import ListQuery, escape_like_literal
from src.core.errors.exceptions import FilteringError
from src.core.utils.datetime_utils import get_utc_now


class QueryModel(SQLAlchemyBase):
    __tablename__ = "query_models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    email: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)

    @hybrid_property
    def computed_created_at(self) -> datetime | None:
        return self.created_at

    @computed_created_at.expression
    def computed_created_at(cls) -> object:  # noqa: N805
        return cls.created_at + timedelta()


SEARCHABLE = ("name", "email")


def compile_clauses(clauses: list[ColumnElement[bool]]) -> Compiled:
    statement = select(QueryModel)
    for clause in clauses:
        statement = statement.where(clause)
    return statement.compile(dialect=postgresql.dialect())


def test_escape_like_literal_escapes_wildcards() -> None:
    assert escape_like_literal(r"100%_match\value") == r"100\%\_match\\value"


def test_build_where_clauses_returns_nothing_for_empty_query() -> None:
    assert ListQuery().build_where_clauses(QueryModel, SEARCHABLE) == []


def test_build_where_clauses_searches_all_searchable_fields_with_or() -> None:
    clauses = ListQuery(search="anne").build_where_clauses(QueryModel, SEARCHABLE)

    compiled = compile_clauses(clauses)

    assert len(clauses) == 1
    assert compiled.string.count("ILIKE") == 2
    assert " OR " in compiled.string
    assert compiled.params["name_1"] == "%anne%"
    assert compiled.params["email_1"] == "%anne%"


def test_build_where_clauses_escapes_search_wildcards() -> None:
    clauses = ListQuery(search=r"100%_match").build_where_clauses(QueryModel, ("name",))

    compiled = compile_clauses(clauses)

    assert r"ESCAPE '\\'" in compiled.string
    assert compiled.params["name_1"] == r"%100\%\_match%"


@pytest.mark.parametrize("search", ["", "   ", None])
def test_build_where_clauses_ignores_blank_search(search: str | None) -> None:
    assert ListQuery(search=search).build_where_clauses(QueryModel, SEARCHABLE) == []


def test_build_where_clauses_rejects_search_when_no_fields_allowed() -> None:
    with pytest.raises(FilteringError):
        ListQuery(search="anne").build_where_clauses(QueryModel, ())


def test_build_where_clauses_applies_inclusive_date_bounds() -> None:
    now = get_utc_now()
    query = ListQuery(date_from=now - timedelta(days=7), date_to=now)

    clauses = query.build_where_clauses(QueryModel, SEARCHABLE)

    compiled = compile_clauses(clauses)

    assert len(clauses) == 2
    assert "created_at >=" in compiled.string
    assert "created_at <=" in compiled.string


def test_build_where_clauses_applies_single_date_bound() -> None:
    query = ListQuery(date_from=get_utc_now() - timedelta(days=1))

    clauses = query.build_where_clauses(QueryModel, SEARCHABLE)

    assert len(clauses) == 1
    assert "created_at >=" in compile_clauses(clauses).string


def test_build_where_clauses_rejects_inverted_date_range() -> None:
    now = get_utc_now()
    query = ListQuery(date_from=now, date_to=now - timedelta(days=1))

    with pytest.raises(FilteringError):
        query.build_where_clauses(QueryModel, SEARCHABLE)


def test_build_where_clauses_rejects_unknown_date_field() -> None:
    query = ListQuery(date_from=get_utc_now(), date_field="published_at")

    with pytest.raises(FilteringError):
        query.build_where_clauses(QueryModel, SEARCHABLE)


def test_build_where_clauses_rejects_non_column_date_field() -> None:
    # A hybrid property is a real, existing attribute (unlike the "unknown
    # date field" case above), but its expression is not column-shaped;
    # comparing it directly would silently build the wrong SQL instead of
    # raising, so it must be rejected the same way as a missing attribute.
    query = ListQuery(date_from=get_utc_now(), date_field="computed_created_at")

    with pytest.raises(FilteringError):
        query.build_where_clauses(QueryModel, SEARCHABLE)


def test_build_where_clauses_ignores_date_field_without_bounds() -> None:
    query = ListQuery(date_field="published_at")

    assert query.build_where_clauses(QueryModel, SEARCHABLE) == []


def test_build_where_clauses_includes_filter_conditions() -> None:
    query = ListQuery(conditions=FilterCondition(gte={"id": 10}))

    clauses = query.build_where_clauses(QueryModel, SEARCHABLE)

    assert len(clauses) == 1
    assert "id >=" in compile_clauses(clauses).string


def test_build_where_clauses_ignores_empty_filter_conditions() -> None:
    query = ListQuery(conditions=FilterCondition())

    assert query.build_where_clauses(QueryModel, SEARCHABLE) == []


def order_by_strings(
    query: ListQuery,
    sortable: tuple[str, ...] = ("created_at", "name"),
    default: str | None = "created_at",
) -> list[str]:
    return [
        str(clause) for clause in query.build_order_by(QueryModel, sortable, default)
    ]


def test_build_order_by_uses_requested_column_and_direction() -> None:
    clauses = order_by_strings(ListQuery(order_by="name", order="asc"))

    assert clauses[0] == "query_models.name ASC NULLS LAST"


def test_build_order_by_appends_primary_key_tiebreaker() -> None:
    # The primary key is NOT NULL by definition, so its tiebreaker clause
    # does not carry `nulls_last()` — unlike the requested/default sort
    # column, which may be nullable.
    clauses = order_by_strings(ListQuery(order_by="name", order="asc"))

    assert clauses[1] == "query_models.id ASC"


def test_build_order_by_does_not_duplicate_primary_key() -> None:
    # Here the primary key *is* the requested sort column, so this exercises
    # `_directed()` (with `nulls_last()`), not the plain tiebreaker clause.
    clauses = order_by_strings(ListQuery(order_by="id"), sortable=("id",))

    assert clauses == ["query_models.id DESC NULLS LAST"]


def test_build_order_by_rejects_field_outside_allowlist() -> None:
    with pytest.raises(FilteringError):
        ListQuery(order_by="email").build_order_by(
            QueryModel, ("created_at", "name"), "created_at"
        )


def test_build_order_by_rejects_any_field_when_sorting_disabled() -> None:
    with pytest.raises(FilteringError):
        ListQuery(order_by="name").build_order_by(QueryModel, (), None)


def test_build_order_by_falls_back_to_default_column() -> None:
    clauses = order_by_strings(ListQuery())

    assert clauses[0] == "query_models.created_at DESC NULLS LAST"
    assert clauses[1] == "query_models.id DESC"


def test_build_order_by_falls_back_to_created_at_without_default() -> None:
    clauses = order_by_strings(ListQuery(), default=None)

    assert clauses[0] == "query_models.created_at DESC NULLS LAST"
    assert clauses[1] == "query_models.id DESC"


def test_build_order_by_falls_back_to_primary_key_without_created_at() -> None:
    class OrderlessModel(SQLAlchemyBase):
        __tablename__ = "orderless_models"

        id: Mapped[int] = mapped_column(Integer, primary_key=True)

    clauses = [
        str(clause) for clause in ListQuery().build_order_by(OrderlessModel, (), None)
    ]

    assert clauses == ["orderless_models.id DESC"]
