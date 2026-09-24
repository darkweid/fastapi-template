from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Any, Literal, cast

from sqlalchemy import inspect, or_
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.sql.elements import ColumnElement, UnaryExpression
from sqlalchemy.sql.expression import ColumnClause

from src.core.database.filters import FilterCondition
from src.core.errors.exceptions import FilteringError

SortOrder = Literal["asc", "desc"]

_ESCAPE_CHAR = "\\"


def escape_like_literal(value: str) -> str:
    """Escape LIKE metacharacters so user input is matched literally.

    Always escapes with `_ESCAPE_CHAR`: `_build_search_clause` hardcodes the
    same character in the `ilike(escape=...)` call, so the two can never
    disagree.
    """
    return (
        value.replace(_ESCAPE_CHAR, _ESCAPE_CHAR * 2)
        .replace("%", f"{_ESCAPE_CHAR}%")
        .replace("_", f"{_ESCAPE_CHAR}_")
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
    date_from: date | datetime | None = None
    date_to: date | datetime | None = None
    date_field: str = "created_at"
    order_by: str | None = None
    order: SortOrder = "desc"
    conditions: FilterCondition | None = None
    # The calendar a bare date or a naive datetime bound is read in. UTC until
    # the project declares a timezone of its own; a service whose users think
    # in local days passes theirs, or "until the 24th" ends hours off.
    local_timezone: tzinfo = UTC

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
        # A missing attribute (`column is None`) and a non-column attribute
        # (a hybrid property, a relationship, a dunder) must fail the same
        # neutral way: comparing either one directly with `>=`/`<=` can raise
        # a non-project exception (`AttributeError`, `NotImplementedError`)
        # or, worse, silently build unintended SQL instead of raising at all.
        if column is None or not isinstance(
            getattr(column, "expression", None), ColumnClause
        ):
            raise FilteringError("Date filtering is not supported for this resource")

        # Both bounds become aware UTC instants before anything else touches
        # them: comparing a naive bound with an aware one raises `TypeError`,
        # which escapes the `FilteringError` handler as a 500, and a naive
        # value bound to a `DateTime(timezone=True)` column is read in the
        # session timezone. A bare `date_to` names a whole day, so it becomes
        # an exclusive bound at the next midnight.
        upper_is_exclusive = self.date_to is not None and not isinstance(
            self.date_to, datetime
        )
        try:
            lower = (
                self._to_utc(self.date_from, next_day=False)
                if self.date_from is not None
                else None
            )
            upper = (
                self._to_utc(self.date_to, next_day=True)
                if self.date_to is not None
                else None
            )
        except OverflowError:
            raise FilteringError(
                "Date range is outside the supported calendar"
            ) from None

        if lower is not None and upper is not None:
            if lower > upper or (upper_is_exclusive and lower == upper):
                raise FilteringError("Date range start must not be later than its end")

        clauses: list[ColumnElement[bool]] = []
        if lower is not None:
            clauses.append(column >= lower)
        if upper is not None:
            clauses.append(column < upper if upper_is_exclusive else column <= upper)
        return clauses

    def _to_utc(self, bound: date | datetime, *, next_day: bool) -> datetime:
        """The UTC instant a bound stands for.

        A datetime is its own instant, a naive one read in `local_timezone`. A
        bare date is local midnight of that day, or of the day after it when
        `next_day` is set. Raises `OverflowError` at the ends of the calendar.
        """
        if isinstance(bound, datetime):
            moment = bound
        else:
            moment = datetime.combine(bound, time.min)
            if next_day:
                moment += timedelta(days=1)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=self.local_timezone)
        return moment.astimezone(UTC)

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
        # string comparison and avoids both traps. `chosen` can be an
        # expression that has no `.name` at all (a hybrid property, a
        # relationship comparator) despite passing `_assert_list_query_fields`
        # for the *searchable* half of a different field, so the lookup is
        # defensive: `getattr(..., "name", None)` instead of a bare attribute
        # access, and the comparison only runs when a name was found.
        chosen = column.expression if column is not None else None
        chosen_name = getattr(chosen, "name", None)
        for primary_key_column in inspect(model).primary_key:
            if chosen_name is not None and primary_key_column.name == chosen_name:
                continue
            clauses.append(self._directed(primary_key_column, nulls_last=False))

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

    def _directed(
        self, column: Any, *, nulls_last: bool = True
    ) -> UnaryExpression[Any]:
        """Order one column in this query's direction.

        `nulls_last=False` is for the primary-key tiebreaker: a primary key is
        `NOT NULL`, so the modifier is a no-op for correctness there and only
        costs a `Sort` node PostgreSQL would otherwise avoid via a plain btree
        index scan.
        """
        ordered = column.asc() if self.order == "asc" else column.desc()
        if nulls_last:
            ordered = ordered.nulls_last()
        # SQLAlchemy's operator mixins type `.asc()`/`.nulls_last()` as
        # `ColumnOperators`, the loosest common return type across all its
        # column-like inputs; the concrete runtime type is a `UnaryExpression`.
        return cast(UnaryExpression[Any], ordered)
