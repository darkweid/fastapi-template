from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy import or_
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.sql.elements import ColumnElement

from src.core.database.filters import FilterCondition
from src.core.errors.exceptions import FilteringError

SortOrder = Literal["asc", "desc"]

_ESCAPE_CHAR = "\\"


def escape_like_literal(value: str, escape_char: str = _ESCAPE_CHAR) -> str:
    """Escape LIKE metacharacters so user input is matched literally."""
    return (
        value.replace(escape_char, escape_char * 2)
        .replace("%", f"{escape_char}%")
        .replace("_", f"{escape_char}_")
    )


@dataclass(frozen=True, slots=True)
class ListQuery:
    """
    Declarative specification of a list query: substring search, date range,
    ordering and comparison conditions.

    The allowed search and sort columns are supplied by the repository, not by
    the caller: `order_by` and `search` come from client input and must never
    reach an arbitrary column.
    """

    search: str | None = None
    date_from: datetime | None = None
    date_to: datetime | None = None
    date_field: str = "created_at"
    order_by: str | None = None
    order: SortOrder = "desc"
    conditions: FilterCondition | None = None

    def build_where_clauses(
        self,
        model: type[DeclarativeBase],
        searchable_fields: Sequence[str],
    ) -> list[ColumnElement[bool]]:
        """Build every WHERE clause this query implies."""
        clauses: list[ColumnElement[bool]] = []

        if self.conditions is not None and self.conditions.has_conditions():
            clauses.extend(self.conditions.build_where_clauses(model))

        clauses.extend(self._build_date_clauses(model))

        search_clause = self._build_search_clause(model, searchable_fields)
        if search_clause is not None:
            clauses.append(search_clause)

        return clauses

    def _build_date_clauses(
        self, model: type[DeclarativeBase]
    ) -> list[ColumnElement[bool]]:
        if self.date_from is None and self.date_to is None:
            return []

        column = getattr(model, self.date_field, None)
        if column is None:
            raise FilteringError("Date filtering is not supported for this resource")

        if (
            self.date_from is not None
            and self.date_to is not None
            and self.date_from > self.date_to
        ):
            raise FilteringError("Date range start must not be later than its end")

        clauses: list[ColumnElement[bool]] = []
        if self.date_from is not None:
            clauses.append(column >= self.date_from)
        if self.date_to is not None:
            clauses.append(column <= self.date_to)
        return clauses

    def _build_search_clause(
        self,
        model: type[DeclarativeBase],
        searchable_fields: Sequence[str],
    ) -> ColumnElement[bool] | None:
        term = (self.search or "").strip()
        if not term:
            return None
        if not searchable_fields:
            raise FilteringError("Search is not supported for this resource")

        pattern = f"%{escape_like_literal(term)}%"
        return or_(
            *(
                getattr(model, field).ilike(pattern, escape=_ESCAPE_CHAR)
                for field in searchable_fields
            )
        )
