from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import ColumnElement
from sqlalchemy.orm import DeclarativeBase


@dataclass(frozen=True, slots=True)
class FilterCondition:
    """
    Typed filter specification for database queries.

    Supports explicit comparison operators via named dictionaries:
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

    _OPERATORS: dict[str, str] = field(
        init=False,
        repr=False,
        default_factory=lambda: {
            "eq": "__eq__",
            "ne": "__ne__",
            "lt": "__lt__",
            "gt": "__gt__",
            "lte": "__le__",
            "gte": "__ge__",
        },
    )

    def validate(self) -> None:
        if not any((self.eq, self.ne, self.lt, self.gt, self.lte, self.gte)):
            raise ValueError("At least one filter condition must be provided")

    def build_where_clauses(
        self, model: type[DeclarativeBase]
    ) -> list[ColumnElement[bool]]:
        self.validate()
        clauses: list[ColumnElement[bool]] = []

        operator_map: dict[str, dict[str, Any]] = {
            "eq": self.eq,
            "ne": self.ne,
            "lt": self.lt,
            "gt": self.gt,
            "lte": self.lte,
            "gte": self.gte,
        }

        for op_name, fields in operator_map.items():
            sa_method = self._OPERATORS[op_name]
            for col_name, value in fields.items():
                column = getattr(model, col_name)
                clauses.append(getattr(column, sa_method)(value))

        return clauses
