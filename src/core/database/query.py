from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, cast

from sqlalchemy import inspect, or_
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.sql.elements import ColumnElement, UnaryExpression

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

    def build_order_by(
        self,
        model: type[DeclarativeBase],
        sortable_fields: Sequence[str],
        default_order_by: str | None,
    ) -> list[UnaryExpression[Any]]:
        """
        Build the ORDER BY clauses, always ending with the primary key.

        The primary key tiebreaker is not optional: limit/offset pagination over
        a non-unique sort column silently duplicates and skips rows between pages.
        """
        column = self._resolve_order_column(model, sortable_fields, default_order_by)
        clauses: list[UnaryExpression[Any]] = []
        if column is not None:
            clauses.append(self._directed(column))

        # `getattr(model, name)` yields an `InstrumentedAttribute`; `.expression`
        # normalizes it to a `Column`-like object comparable with the raw `Column`
        # objects from `inspect(model).primary_key`. The two are never the same
        # Python object (the ORM wraps the mapped column in an annotated proxy),
        # so identity comparison always fails; `==` would build a SQL expression
        # instead of a boolean. Comparing the plain `.name` attribute is a normal
        # string comparison and avoids both traps.
        chosen = column.expression if column is not None else None
        for primary_key_column in inspect(model).primary_key:
            if chosen is not None and primary_key_column.name == chosen.name:
                continue
            clauses.append(self._directed(primary_key_column))

        return clauses

    def _resolve_order_column(
        self,
        model: type[DeclarativeBase],
        sortable_fields: Sequence[str],
        default_order_by: str | None,
    ) -> Any:
        if self.order_by is not None:
            if self.order_by not in sortable_fields:
                raise FilteringError("Ordering by the requested field is not supported")
            return getattr(model, self.order_by)

        for candidate in (default_order_by, "created_at"):
            if candidate is None:
                continue
            column = getattr(model, candidate, None)
            if column is not None:
                return column
        return None

    def _directed(self, column: Any) -> UnaryExpression[Any]:
        ordered = column.asc() if self.order == "asc" else column.desc()
        # SQLAlchemy's operator mixins type `.nulls_last()` as `ColumnOperators`,
        # the loosest common return type across all its column-like inputs; the
        # concrete runtime type is always a `UnaryExpression`.
        return cast(UnaryExpression[Any], ordered.nulls_last())
