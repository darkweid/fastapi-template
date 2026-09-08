from sqlalchemy import Integer, String
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from src.core.database.base import Base as SQLAlchemyBase
from src.core.database.filters import _FILTER_OPERATORS, FilterCondition
from src.core.errors.exceptions import FilteringError


class FilterModel(SQLAlchemyBase):
    __tablename__ = "filter_models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64))


def test_every_filter_field_has_an_operator_and_vice_versa() -> None:
    # The dataclass fields and the operator table are written apart; a member
    # added to one and forgotten in the other raises a KeyError only when a
    # client happens to use that operator.
    assert set(FilterCondition.__dataclass_fields__) == set(_FILTER_OPERATORS)


def test_filter_condition_build_where_clauses_returns_sqlalchemy_clauses() -> None:
    clauses = FilterCondition(eq={"id": 1}, ne={"name": "alpha"}).build_where_clauses(
        FilterModel
    )

    compiled = [
        str(
            clause.compile(
                dialect=postgresql.dialect(),
                compile_kwargs={"literal_binds": True},
            )
        )
        for clause in clauses
    ]

    assert compiled == ["filter_models.id = 1", "filter_models.name != 'alpha'"]


def test_filter_condition_build_where_clauses_raises_filtering_error_for_unknown_column() -> (
    None
):
    condition = FilterCondition(eq={"unknown_field": 1})

    try:
        condition.build_where_clauses(FilterModel)
    except FilteringError as exc:
        assert exc.message == (
            "Unknown filter column 'unknown_field' for model 'FilterModel'"
        )
    else:
        raise AssertionError("FilteringError was not raised for an unknown column")
