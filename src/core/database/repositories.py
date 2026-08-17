from typing import Any, Generic, TypeVar, cast

from sqlalchemy import (
    Enum as SAEnum,
    String,
    Table,
    func,
    inspect as sqlalchemy_inspect,
    select,
    update,
)
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement
from sqlalchemy.sql.expression import ColumnClause

from loggers import get_logger
from src.core.database.base import Base as SQLAlchemyBase
from src.core.database.filters import FilterCondition
from src.core.database.mixins import SoftDeleteMixin
from src.core.database.query import ListQuery, SortOrder
from src.core.database.transactions import advisory_xact_lock, try_advisory_xact_lock
from src.core.database.types import EagerLoadSequence
from src.core.utils.datetime_utils import get_utc_now

logger = get_logger(__name__)

T = TypeVar("T", bound=SQLAlchemyBase)


class BaseRepository(Generic[T]):
    """Base repository with common SQLAlchemy operations using context-managed sessions."""

    model: type[T]

    # Columns a client is allowed to search and sort by. Empty by default:
    # `search` and `order_by` arrive from client input, so a domain opts in
    # explicitly instead of exposing every column of its table.
    searchable_fields: tuple[str, ...] = ()
    sortable_fields: tuple[str, ...] = ()
    default_order_by: str | None = None

    def __init__(self) -> None:
        if not hasattr(self, "model"):
            raise NotImplementedError("Subclasses must define class variable 'model'")
        self._assert_list_query_fields()

    def _assert_list_query_fields(self) -> None:
        """
        Fail when the repository is constructed (per request, via `Depends()`)
        if a declared searchable/sortable column is wrong.

        A misdeclared column would otherwise surface as a 500 on the first
        request: `ilike` against a non-text column is a PostgreSQL error.
        """
        orderable_fields = (
            *self.sortable_fields,
            *((self.default_order_by,) if self.default_order_by else ()),
        )
        for field in orderable_fields:
            attribute = getattr(self.model, field, None)
            expression = getattr(attribute, "expression", None)
            # A hybrid property or a relationship can have a typed
            # `.expression` too, but neither is a real column: `build_order_by`
            # cannot dedupe them against the primary key by `.name`, and
            # `.desc()`/`.asc()` on a relationship comparator raises
            # `NotImplementedError` at query time instead of failing here.
            # Requiring a `ColumnClause`-shaped expression catches both.
            if not isinstance(expression, ColumnClause):
                raise TypeError(
                    f"{self.model.__name__} has no orderable attribute '{field}'"
                )

        for field in self.searchable_fields:
            attribute = getattr(self.model, field, None)
            column_type = getattr(getattr(attribute, "expression", None), "type", None)
            if column_type is None:
                raise TypeError(
                    f"{self.model.__name__} has no searchable attribute '{field}'"
                )
            if not isinstance(column_type, String) or isinstance(column_type, SAEnum):
                raise TypeError(
                    f"{self.model.__name__}.{field} must be a text column to be "
                    "searchable"
                )

    @staticmethod
    def _ensure_commit_allowed(session: AsyncSession) -> None:
        # A UoW owns its transaction: a direct commit under it would end the
        # transaction while the UoW still believes it is open (rollback becomes
        # impossible, uow.commit() would later "succeed" on nothing).
        if session.info.get("uow_active"):
            raise RuntimeError(
                "commit=True inside an active UnitOfWork: the UoW owns the "
                "transaction. Call the repository with commit=False and let "
                "the UoW commit."
            )

    async def create(
        self, session: AsyncSession, data: dict[str, Any], commit: bool = False
    ) -> T:
        """Create a new record using the provided session."""
        if commit:
            self._ensure_commit_allowed(session)
        try:
            instance = self.model(**data)
            session.add(instance)
            if commit:
                await session.commit()
                await session.refresh(instance)
                logger.debug(
                    "%s created successfully [Committed].", self.model.__name__
                )
            else:
                logger.debug(
                    "%s created [Staged, pending commit].", self.model.__name__
                )
            return instance
        except (IntegrityError, SQLAlchemyError):
            if commit:
                await session.rollback()
            raise

    async def xact_lock(self, session: AsyncSession, key: str) -> None:
        """
        Acquire an advisory transaction lock for the given string key.
        """
        await advisory_xact_lock(session, self._namespaced_lock_key(key))

    async def try_xact_lock(self, session: AsyncSession, key: str) -> bool:
        """
        Try to acquire an advisory transaction lock for the given string key.

        Returns True if the lock was acquired, False otherwise.
        """
        return await try_advisory_xact_lock(session, self._namespaced_lock_key(key))

    def _namespaced_lock_key(self, key: str) -> str:
        """
        Prefix the lock key with model identity to avoid cross-repo collisions.
        """
        table_name = getattr(self.model, "__tablename__", None)
        model_name = table_name or self.model.__name__
        return f"{model_name}:{key}"

    async def exists(
        self, session: AsyncSession, strict_single: bool = False, **filters: Any
    ) -> bool:
        """
        Determine if a record exists in the database matching the provided filters.
        Optionally, it can enforce strict single-record existence checks.
        """
        if strict_single:
            query = select(1).select_from(self.model).filter_by(**filters).limit(2)
            rows = (await session.execute(query)).all()
            return len(rows) == 1
        else:
            subquery = select(1).select_from(self.model).filter_by(**filters).limit(1)
            query = select(subquery.exists())
            return bool(await session.scalar(query))

    async def get_single(
        self,
        session: AsyncSession,
        eager: EagerLoadSequence | None = None,
        for_update: bool = False,
        **filters: Any,
    ) -> T | None:
        """Retrieve a single record using the provided session."""
        query = select(self.model).filter_by(**filters).limit(1)

        if eager:
            query = query.options(*eager)
        if for_update:
            query = self._apply_for_update(query)

        result = await session.execute(query)
        return result.unique().scalars().first()

    async def get_list(
        self,
        session: AsyncSession,
        eager: EagerLoadSequence | None = None,
        for_update: bool = False,
        **filters: Any,
    ) -> list[T]:
        """Retrieve a list of records using the provided session without pagination."""
        query = select(self.model).filter_by(**filters)
        if eager:
            query = query.options(*eager)
        if for_update:
            query = self._apply_for_update(query)
        query = self._apply_default_ordering(query)

        result = await session.execute(query)
        return list(result.unique().scalars().all())

    async def get_paginated_list(
        self,
        session: AsyncSession,
        page: int,
        size: int,
        eager: EagerLoadSequence | None = None,
        query: ListQuery | None = None,
        **filters: Any,
    ) -> tuple[list[T], int]:
        """Retrieve a paginated list of records using limit/offset pagination."""
        if page < 1:
            raise ValueError("page must be greater than or equal to 1")
        if size < 1:
            raise ValueError("size must be greater than or equal to 1")

        list_query = query if query is not None else ListQuery()
        where_clauses = list_query.build_where_clauses(
            self.model, self.searchable_fields
        )
        order_by = list_query.build_order_by(
            self.model, self.sortable_fields, self.default_order_by
        )

        statement = select(self.model).filter_by(**filters).where(*where_clauses)
        if eager:
            statement = statement.options(*eager)
        statement = statement.order_by(*order_by)
        statement = statement.offset((page - 1) * size).limit(size)

        result = await session.execute(statement)
        items = list(result.unique().scalars().all())

        # The count must carry the same predicates as the selection, or `total`
        # and `items` describe different result sets. Eager options are left out:
        # they add joins a count does not need.
        count_statement = (
            select(func.count())
            .select_from(self.model)
            .filter_by(**filters)
            .where(*where_clauses)
        )
        total = int((await session.execute(count_statement)).scalar_one())

        return items, total

    async def count(
        self,
        session: AsyncSession,
        **filters: Any,
    ) -> int:
        """Count records matching the provided filters using the given session."""
        query = select(func.count()).select_from(self.model).filter_by(**filters)
        result = await session.execute(query)
        count_value = result.scalar_one()
        return int(count_value)

    async def update(
        self,
        session: AsyncSession,
        data: dict[str, Any],
        commit: bool = False,
        **filters: Any,
    ) -> T | None:
        """Update a record using the provided session."""
        if commit:
            self._ensure_commit_allowed(session)
        self._ensure_filters_present(filters)
        # setattr with a mistyped key would silently attach a plain Python
        # attribute the flush ignores - the caller believes the row changed.
        # Boundary: mapped attributes (columns and relationships) pass; hybrid
        # properties and plain Python properties are rejected even when they
        # have setters - a domain that needs to set one extends this guard
        # deliberately instead of inheriting a silent hole.
        unknown_fields = set(data) - set(sqlalchemy_inspect(self.model).attrs.keys())
        if unknown_fields:
            raise ValueError(
                f"Unknown fields for {self.model.__name__}: {sorted(unknown_fields)}"
            )
        try:
            query = select(self.model).filter_by(**filters)
            result = await session.execute(query)
            instance = result.scalars().first()
            if instance:
                for key, value in data.items():
                    setattr(instance, key, value)
                if commit:
                    await session.commit()
                    await session.refresh(instance)
                    logger.debug(
                        "%s updated successfully [Committed].", self.model.__name__
                    )
                else:
                    logger.debug(
                        "%s updated [Staged, pending commit].", self.model.__name__
                    )
                return instance

            logger.debug(
                "%s update skipped [NotFound]. filters=%s", self.model.__name__, filters
            )
            return None
        except (IntegrityError, SQLAlchemyError):
            if commit:
                await session.rollback()
            raise

    async def delete(
        self, session: AsyncSession, commit: bool = False, **filters: Any
    ) -> T | None:
        """Delete a record using the provided session."""
        if commit:
            self._ensure_commit_allowed(session)
        self._ensure_filters_present(filters)
        try:
            query = select(self.model).filter_by(**filters)
            result = await session.execute(query)
            instance = result.scalars().first()
            if instance:
                await session.delete(instance)
                if commit:
                    await session.commit()
                    logger.debug(
                        "%s deleted successfully [Committed].", self.model.__name__
                    )
                else:
                    logger.debug(
                        "%s deleted [Staged, pending commit].", self.model.__name__
                    )
                return instance
            return None
        except (IntegrityError, SQLAlchemyError):
            if commit:
                await session.rollback()
            raise

    @staticmethod
    def _ensure_filters_present(filters: dict[str, Any]) -> None:
        if not filters:
            raise ValueError("At least one filter must be provided for update/delete")

    def _apply_for_update(self, query: Any) -> Any:
        """Limit row locking to the current model table to avoid outer-join issues."""
        # cast, not plain attribute access: SQLAlchemy's declarative stubs type
        # __table__ narrowly enough that direct access loses .primary_key.columns.
        table = cast(Table, self.model.__table__)
        pk_columns = tuple(
            cast("ColumnElement[Any]", column) for column in table.primary_key.columns
        )
        if pk_columns:
            return query.with_for_update(of=pk_columns)
        return query.with_for_update(of=(table,))

    def _apply_default_ordering(
        self,
        query: Any,
        order: SortOrder = "desc",
    ) -> Any:
        """Apply default ordering by created_at, falling back to id."""
        order_by = getattr(self.model, "created_at", None)
        if order_by is None:
            order_by = getattr(self.model, "id", None)
        if order_by is None:
            return query
        if order == "asc":
            return query.order_by(order_by.asc())
        return query.order_by(order_by.desc())


class SoftDeleteRepository(BaseRepository[T], Generic[T]):
    """Repository with soft delete support."""

    def __init__(self) -> None:
        super().__init__()
        self._assert_softdelete_fields()

    async def exists(
        self, session: AsyncSession, strict_single: bool = False, **filters: Any
    ) -> bool:
        filters.setdefault("is_deleted", False)
        return await super().exists(session, strict_single=strict_single, **filters)

    async def get_single(
        self,
        session: AsyncSession,
        eager: EagerLoadSequence | None = None,
        for_update: bool = False,
        **filters: Any,
    ) -> T | None:
        """Retrieve a single record where the is_deleted flag is False, using the provided session and filters."""
        filters.setdefault("is_deleted", False)
        return await super().get_single(
            session, eager=eager, for_update=for_update, **filters
        )

    async def get_list(
        self,
        session: AsyncSession,
        eager: EagerLoadSequence | None = None,
        for_update: bool = False,
        **filters: Any,
    ) -> list[T]:
        """Retrieve a list of records where the is_deleted flag is False, using the provided session and filters."""
        filters.setdefault("is_deleted", False)
        return await super().get_list(
            session, eager=eager, for_update=for_update, **filters
        )

    async def get_paginated_list(
        self,
        session: AsyncSession,
        page: int,
        size: int,
        eager: EagerLoadSequence | None = None,
        query: ListQuery | None = None,
        **filters: Any,
    ) -> tuple[list[T], int]:
        """Retrieve a list of records where is_deleted flag is False, using the filters,
        with pagination."""
        filters.setdefault("is_deleted", False)
        return await super().get_paginated_list(
            session, page=page, size=size, eager=eager, query=query, **filters
        )

    async def count(
        self,
        session: AsyncSession,
        **filters: Any,
    ) -> int:
        """Count records matching the provided filters using the given session."""
        filters.setdefault("is_deleted", False)
        return await super().count(session, **filters)

    async def update(
        self,
        session: AsyncSession,
        data: dict[str, Any],
        commit: bool = False,
        **filters: Any,
    ) -> T | None:
        """Update a record where is_deleted flag is False, using the filters."""
        # Guard before the setdefault below: seeding is_deleted first would make
        # the base-class filter check pass for a filter-less call.
        self._ensure_filters_present(filters)
        filters.setdefault("is_deleted", False)
        return await super().update(session, data, commit, **filters)

    async def delete(
        self, session: AsyncSession, commit: bool = False, **filters: Any
    ) -> T | None:
        """Soft delete a record, using the filters."""
        self._ensure_filters_present(filters)
        if commit:
            self._ensure_commit_allowed(session)
        filters.setdefault("is_deleted", False)
        try:
            query = select(self.model).filter_by(**filters)
            result = await session.execute(query)
            instance: T | None = result.scalars().first()
            if instance:
                # cast, not plain attribute access: T is bound to the declarative
                # base only, not to SoftDeleteMixin, so mypy cannot see these
                # fields on it; _assert_softdelete_fields() guarantees they
                # exist on the actual model at runtime.
                cast(SoftDeleteMixin, instance).is_deleted = True
                cast(SoftDeleteMixin, instance).deleted_at = get_utc_now()
                if commit:
                    await session.commit()
                    await session.refresh(instance)
                    logger.debug(
                        "%s soft-deleted successfully [Committed].", self.model.__name__
                    )
                else:
                    logger.debug(
                        "%s soft-deleted [Staged, pending commit].", self.model.__name__
                    )
                return instance
            return None
        except (IntegrityError, SQLAlchemyError):
            if commit:
                await session.rollback()
            raise

    async def batch_soft_delete(
        self,
        session: AsyncSession,
        filters: FilterCondition,
        commit: bool = False,
    ) -> int:
        """Soft delete multiple records matching the typed filter conditions."""
        if commit:
            self._ensure_commit_allowed(session)
        if not filters.has_conditions():
            raise ValueError("At least one filter condition must be provided")
        merged = FilterCondition(
            eq={**{"is_deleted": False}, **filters.eq},
            ne=filters.ne,
            lt=filters.lt,
            gt=filters.gt,
            lte=filters.lte,
            gte=filters.gte,
        )
        try:
            stmt = update(self.model).values(is_deleted=True, deleted_at=get_utc_now())
            for clause in merged.build_where_clauses(self.model):
                stmt = stmt.where(clause)

            result = await session.execute(stmt)
            count = int(result.rowcount) if hasattr(result, "rowcount") else 0

            if commit:
                await session.commit()
                logger.debug(
                    "%s batch soft-deleted %d record(s) [Committed].",
                    self.model.__name__,
                    count,
                )
            else:
                logger.debug(
                    "%s batch soft-deleted %d record(s) [Staged, pending commit].",
                    self.model.__name__,
                    count,
                )
            return count
        except SQLAlchemyError:
            if commit:
                await session.rollback()
            logger.exception("Batch soft-delete failed for %s", self.model.__name__)
            raise

    def _assert_softdelete_fields(self) -> None:
        if not hasattr(self.model, "is_deleted") or not hasattr(
            self.model, "deleted_at"
        ):
            raise TypeError(
                f"{self.model.__name__} must define 'is_deleted' and 'deleted_at' for SoftDeleteRepository"
            )
