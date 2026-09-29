"""What `detached_read` leaves behind on a real session.

A SAVEPOINT rollback does not expire instances it never touched, so only a
top-level rollback on a real engine shows whether the detached graph survives,
and only a real pool shows whether the connection went back.
"""

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import ForeignKey, String, func, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    attribute_keyed_dict,
    mapped_column,
    relationship,
    selectinload,
)

from src.core.database.detached import detached_read
from tests.integration.conftest import ScratchDatabase

pytestmark = pytest.mark.asyncio(loop_scope="session")


class GraphBase(DeclarativeBase):
    """Models of this test alone: the template ships no relationship to walk."""


class Author(GraphBase):
    __tablename__ = "detached_authors"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))


class Shelf(GraphBase):
    __tablename__ = "detached_shelves"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50))
    books: Mapped[list["Book"]] = relationship(lazy="raise", order_by="Book.id")
    books_by_title: Mapped[dict[str, "Book"]] = relationship(
        collection_class=attribute_keyed_dict("title"), lazy="raise", viewonly=True
    )


class Book(GraphBase):
    __tablename__ = "detached_books"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(50))
    shelf_id: Mapped[int] = mapped_column(ForeignKey("detached_shelves.id"))
    author_id: Mapped[int] = mapped_column(ForeignKey("detached_authors.id"))
    author: Mapped[Author] = relationship(lazy="raise")


@pytest_asyncio.fixture(loop_scope="session")
async def engine(scratch_database: ScratchDatabase) -> AsyncGenerator[AsyncEngine]:
    engine = scratch_database.engine
    async with engine.begin() as connection:
        await connection.run_sync(GraphBase.metadata.create_all)
    async with AsyncSession(engine) as session:
        author = Author(id=1, name="Author")
        session.add_all(
            [
                author,
                Shelf(id=1, name="Shelf"),
                Book(id=1, title="First", shelf_id=1, author=author),
                Book(id=2, title="Second", shelf_id=1, author=author),
            ]
        )
        await session.commit()
    yield engine


async def _load_shelf(session: AsyncSession) -> Shelf:
    return (
        await session.execute(
            select(Shelf)
            .where(Shelf.id == 1)
            .options(
                selectinload(Shelf.books).selectinload(Book.author),
                selectinload(Shelf.books_by_title).selectinload(Book.author),
            )
        )
    ).scalar_one()


async def test_detached_graph_stays_readable_after_a_top_level_rollback(
    engine: AsyncEngine,
) -> None:
    """A rollback expires every instance its session holds, and reading an
    expired attribute lazy-loads outside any greenlet. `expunge` does not
    cascade along relationships, so the eager-loaded rows under the root are
    as exposed as the root itself."""
    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with detached_read(session) as read:
            shelf = await _load_shelf(session)
            read.detach(shelf)

        await session.execute(text("SELECT 1"))
        await session.rollback()

        assert shelf.name == "Shelf"
        assert [(book.title, book.author.name) for book in shelf.books] == [
            ("First", "Author"),
            ("Second", "Author"),
        ]
        assert {
            title: book.author.name for title, book in shelf.books_by_title.items()
        } == {"First": "Author", "Second": "Author"}


async def test_leaves_a_transaction_it_did_not_open(engine: AsyncEngine) -> None:
    """Rolling back someone else's transaction would discard the work it has
    flushed so far."""
    async with AsyncSession(engine, expire_on_commit=False) as session:
        session.add(Shelf(id=2, name="Unsaved"))
        await session.flush()

        async with detached_read(session) as read:
            read.detach(await _load_shelf(session))

        assert session.in_transaction()
        count = await session.scalar(select(func.count()).select_from(Shelf))
        assert count == 2
        await session.rollback()


async def test_ends_the_transaction_it_opened_and_gives_the_connection_back(
    engine: AsyncEngine,
) -> None:
    async with AsyncSession(engine, expire_on_commit=False) as session:
        async with detached_read(session) as read:
            read.detach(await _load_shelf(session))

        assert not session.in_transaction()
        assert engine.pool.checkedout() == 0  # type: ignore[attr-defined]


async def test_ends_it_when_the_block_raises(engine: AsyncEngine) -> None:
    """A dependency answering 404 after its read must not hold the connection
    until the error response is written."""
    async with AsyncSession(engine, expire_on_commit=False) as session:
        with pytest.raises(LookupError):
            async with detached_read(session):
                await _load_shelf(session)
                raise LookupError

        assert not session.in_transaction()
        assert engine.pool.checkedout() == 0  # type: ignore[attr-defined]
