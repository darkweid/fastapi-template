from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, tzinfo
from typing import Any, Literal, NamedTuple, cast

from sqlalchemy import SQLColumnExpression, and_, func, inspect, literal_column, or_
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


# Each word is an OR across every searchable column, correlated subqueries
# included, so the count of words multiplies the work per row.
MAX_SEARCH_WORDS = 5


def fold_yo(text: str) -> str:
    """`ё` read as `е`: people type both for one letter."""
    return text.replace("ё", "е").replace("Ё", "Е")


def searchable_text(
    column: SQLColumnExpression[Any],
) -> SQLColumnExpression[Any]:
    """The text the search matches: `column` with `ё` read as `е`. The two
    alphabets are literals, never bound parameters: a trigram index over this
    expression matches a query only when the expression is spelled the same,
    and under a generic plan a parameter is not."""
    return func.translate(column, literal_column("'Ёё'"), literal_column("'Ее'"))


def search_words(term: str) -> list[str]:
    """The distinct words of a search in first-seen order, with `ё` read as
    `е` and letter case ignored as `ilike` ignores it, at most
    `MAX_SEARCH_WORDS`."""
    words = {word.casefold(): word for word in reversed(fold_yo(term).split())}
    return list(reversed(words.values()))[:MAX_SEARCH_WORDS]


class RelatedSearch(NamedTuple):
    """A table the list search reads beyond the model's own columns, joined
    `LEFT OUTER` on `onclause` only while a search is active. The join must
    reach at most one row per model row (a parent, never a child
    collection), or the page and its count multiply."""

    target: Any
    onclause: ColumnElement[bool]
    columns: Sequence[SQLColumnExpression[Any]]


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

    @property
    def has_search(self) -> bool:
        return bool(search_words(self.search or ""))

    def build_where_clauses(
        self,
        model: type[DeclarativeBase],
        searchable_fields: Sequence[str],
        related_search_columns: Sequence[SQLColumnExpression[Any]] = (),
    ) -> list[ColumnElement[bool]]:
        """Build every WHERE clause this query implies.

        `related_search_columns` are columns of the tables a `RelatedSearch`
        joins: the name a row shows may live on another table, and a search
        that cannot see it finds nothing by the name on the screen. The
        caller adds the joins."""
        clauses: list[ColumnElement[bool]] = []

        if self.conditions is not None and self.conditions.has_conditions():
            clauses.extend(self.conditions.build_where_clauses(model))

        clauses.extend(self._build_date_clauses(model))

        search_clause = self._build_search_clause(
            model, searchable_fields, related_search_columns
        )
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
        related_search_columns: Sequence[SQLColumnExpression[Any]],
    ) -> ColumnElement[bool] | None:
        """Every word of the search must be found in some searchable column,
        in any order: "Ivan Petrov" finds the row whose first name holds one
        word and last name the other, which a match of the whole phrase
        against each column alone never does."""
        words = search_words(self.search or "")
        if not words:
            return None
        columns = [
            *(getattr(model, field) for field in searchable_fields),
            *related_search_columns,
        ]
        if not columns:
            raise FilteringError("Search is not supported for this resource")

        return and_(
            *(
                or_(
                    *(
                        searchable_text(column).ilike(
                            f"%{escape_like_literal(word)}%", escape=_ESCAPE_CHAR
                        )
                        for column in columns
                    )
                )
                for word in words
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
        """Order one column in this query's direction, NULLs last.

        `NULLS LAST` is added only where a NULL can occur. On a `NOT NULL`
        column it changes no result, but `DESC NULLS LAST` does not match a
        plain ascending btree scanned backwards (that yields `NULLS FIRST`), so
        it would turn an index scan into a `Sort` over the whole table. An
        expression whose nullability is unknown (a hybrid property) keeps it.
        """
        ordered = column.asc() if self.order == "asc" else column.desc()
        if _may_be_null(column):
            ordered = ordered.nulls_last()
        # SQLAlchemy's operator mixins type `.asc()`/`.nulls_last()` as
        # `ColumnOperators`, the loosest common return type across all its
        # column-like inputs; the concrete runtime type is a `UnaryExpression`.
        return cast(UnaryExpression[Any], ordered)


def _may_be_null(column: Any) -> bool:
    expression = getattr(column, "expression", column)
    return getattr(expression, "nullable", None) is not False
