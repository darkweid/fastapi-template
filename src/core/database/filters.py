from collections.abc import Callable
from dataclasses import dataclass, field, fields
import operator
from typing import Any

from sqlalchemy import ColumnElement
from sqlalchemy.orm import DeclarativeBase

from src.core.errors.exceptions import FilteringError

# Field name -> the comparison it builds. The dataclass fields below are named
# after these keys, so adding an operator means adding both together.
_FILTER_OPERATORS: dict[str, Callable[[Any, Any], Any]] = {
    "eq": operator.eq,
    "ne": operator.ne,
    "lt": operator.lt,
    "gt": operator.gt,
    "lte": operator.le,
    "gte": operator.ge,
}


@dataclass(frozen=True, slots=True)
class FilterCondition:
    """
    Typed filter specification for database queries.

    Each field maps column name -> value under one comparison operator:
      eq:  field == value
      ne:  field != value
      lt:  field < value
      gt:  field > value
      lte: field <= value
      gte: field >= value
    """

    eq: dict[str, Any] = field(default_factory=dict)
    ne: dict[str, Any] = field(default_factory=dict)
    lt: dict[str, Any] = field(default_factory=dict)
    gt: dict[str, Any] = field(default_factory=dict)
    lte: dict[str, Any] = field(default_factory=dict)
    gte: dict[str, Any] = field(default_factory=dict)

    def _conditions(self) -> list[tuple[str, dict[str, Any]]]:
        return [(f.name, getattr(self, f.name)) for f in fields(self)]

    def has_conditions(self) -> bool:
        return any(values for _, values in self._conditions())

    def validate(self) -> None:
        if not self.has_conditions():
            raise ValueError("At least one filter condition must be provided")

    def build_where_clauses(
        self, model: type[DeclarativeBase]
    ) -> list[ColumnElement[bool]]:
        self.validate()
        clauses: list[ColumnElement[bool]] = []

        for operator_name, values in self._conditions():
            compare = _FILTER_OPERATORS[operator_name]
            for column_name, value in values.items():
                column = getattr(model, column_name, None)
                if column is None:
                    raise FilteringError(
                        f"Unknown filter column '{column_name}' for model "
                        f"'{model.__name__}'"
                    )
                clauses.append(compare(column, value))

        return clauses
