"""A note or profile write against a change committed while it waits.

A second session holds the row with its change uncommitted; the write reaches
the lock PostgreSQL reports, then the change commits. The write's own session
has already loaded the old row, so it also shows a read that kept stale values.
"""

import asyncio
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from src.core.cache.memory_cache import InMemoryCache
from src.core.database.repositories import BaseRepository
from src.core.database.uow import ApplicationUnitOfWork
from src.core.errors.exceptions import InstanceNotFoundException
from src.event_log.actor import Actor
from src.event_log.models import EventLog
from src.note.models import Note
from src.note.repositories import NoteRepository
from src.note.schemas import NoteUpdateModel
from src.note.usecases.delete_note import DeleteNoteUseCase
from src.note.usecases.update_note import UpdateNoteUseCase
from src.user.models import User
from src.user.repositories import UserRepository
from src.user.schemas import UserProfileUpdateModel
from src.user.usecases.update_profile import UpdateUserProfileUseCase
from tests.factories.user_factory import build_user
from tests.helpers.races import WAIT_SECONDS, wait_for_lock_wait

pytestmark = pytest.mark.asyncio(loop_scope="session")


class HardDeleteNoteRepository(BaseRepository[Note]):
    model = Note


class Race:
    def __init__(self, engine: AsyncEngine, owner: User, heir: User, note: Note):
        self.engine = engine
        self.owner = owner
        self.heir = heir
        self.note = note
        self.actor = Actor.user(owner.id)
        self.sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def __call__(
        self,
        model: type[Note | User],
        change: dict[str, Any],
        write: Callable[[AsyncSession], Awaitable[Any]],
    ) -> Any:
        """The result of `write`, forced to wait for `change` to commit."""
        row = self.note if model is Note else self.owner
        async with self.sessions() as holder, self.sessions() as session:
            stale = await session.get(model, row.id)
            assert stale is not None
            await session.commit()
            await holder.execute(
                update(model).where(model.id == row.id).values(**change)
            )
            writing = asyncio.ensure_future(write(session))
            try:
                await wait_for_lock_wait(self.engine, model.__tablename__)
            finally:
                await holder.commit()
                await asyncio.wait([writing])
            return await writing

    async def stored(self, model: type[Any]) -> Any:
        row = self.note if model is Note else self.owner
        async with self.sessions() as session:
            return await session.get(model, row.id)

    async def events(self) -> list[str]:
        async with self.sessions() as session:
            rows = await session.execute(
                select(EventLog.event_type).where(
                    EventLog.object_id.in_([self.note.id, self.owner.id])
                )
            )
            return list(rows.scalars())


@pytest_asyncio.fixture(loop_scope="session")
async def race(integration_engine: AsyncEngine) -> AsyncGenerator[Race]:
    owner, heir = (
        build_user(email=f"{uuid4().hex}@example.com", username=uuid4().hex)
        for _ in range(2)
    )
    async with AsyncSession(integration_engine, expire_on_commit=False) as session:
        session.add_all([owner, heir])
        await session.flush()
        note = Note(title="Old", content="", owner_id=owner.id)
        session.add(note)
        await session.commit()
    yield Race(integration_engine, owner, heir, note)
    async with AsyncSession(integration_engine) as session:
        ids = [note.id, owner.id]
        await session.execute(delete(EventLog).where(EventLog.object_id.in_(ids)))
        await session.execute(delete(Note).where(Note.owner_id == owner.id))
        await session.execute(delete(User).where(User.id.in_([owner.id, heir.id])))
        await session.commit()


async def test_a_note_update_records_what_it_changed_on_the_locked_row(
    race: Race,
) -> None:
    """Both writers set one title; the second changed nothing and records nothing."""
    result = await race(
        Note,
        {"title": "New"},
        lambda session: UpdateNoteUseCase(ApplicationUnitOfWork(session)).execute(
            race.note.id, NoteUpdateModel(title="New"), race.owner, race.actor
        ),
    )

    assert result.title == "New"
    assert await race.events() == []


async def test_a_note_update_keeps_the_fields_it_does_not_send(race: Race) -> None:
    result = await race(
        Note,
        {"content": "Written meanwhile"},
        lambda session: UpdateNoteUseCase(ApplicationUnitOfWork(session)).execute(
            race.note.id, NoteUpdateModel(title="New"), race.owner, race.actor
        ),
    )

    assert (result.title, result.content) == ("New", "Written meanwhile")


async def test_a_note_update_is_refused_once_the_note_changed_hands(
    race: Race,
) -> None:
    with pytest.raises(InstanceNotFoundException):
        await race(
            Note,
            {"owner_id": race.heir.id},
            lambda session: UpdateNoteUseCase(ApplicationUnitOfWork(session)).execute(
                race.note.id, NoteUpdateModel(title="New"), race.owner, race.actor
            ),
        )

    assert (await race.stored(Note)).title == "Old"


async def test_a_note_delete_is_refused_once_the_note_changed_hands(
    race: Race,
) -> None:
    with pytest.raises(InstanceNotFoundException):
        await race(
            Note,
            {"owner_id": race.heir.id},
            lambda session: DeleteNoteUseCase(ApplicationUnitOfWork(session)).execute(
                race.note.id, race.owner, race.actor
            ),
        )

    assert (await race.stored(Note)).is_deleted is False
    assert await race.events() == []


async def test_a_profile_update_records_what_it_changed_on_the_locked_row(
    race: Race, cache: InMemoryCache
) -> None:
    result = await race(
        User,
        {"first_name": "Grace"},
        lambda session: UpdateUserProfileUseCase(
            ApplicationUnitOfWork(session), cache
        ).execute(
            UserProfileUpdateModel(first_name="Grace"), race.owner.id, race.actor
        ),
    )

    assert result.first_name == "Grace"
    assert await race.events() == []


async def test_a_repository_update_writes_nothing_its_filters_no_longer_match(
    race: Race,
) -> None:
    async def write(session: AsyncSession) -> Note | None:
        async with session.begin():
            return await NoteRepository().update(
                session, {"title": "New"}, id=race.note.id, owner_id=race.owner.id
            )

    assert await race(Note, {"owner_id": race.heir.id}, write) is None
    assert (await race.stored(Note)).title == "Old"


async def test_a_repository_update_restores_a_value_changed_meanwhile(
    race: Race,
) -> None:
    """The session still holds "Old": assigning it to a stale instance is no
    change, so no UPDATE would be sent and the caller's write would be lost."""

    async def write(session: AsyncSession) -> None:
        async with session.begin():
            await NoteRepository().update(session, {"title": "Old"}, id=race.note.id)

    await race(Note, {"title": "Meanwhile"}, write)

    assert (await race.stored(Note)).title == "Old"


@pytest.mark.parametrize(
    "repository", [NoteRepository(), HardDeleteNoteRepository()], ids=["soft", "hard"]
)
async def test_a_repository_delete_deletes_nothing_its_filters_no_longer_match(
    race: Race, repository: BaseRepository[Note]
) -> None:
    async def write(session: AsyncSession) -> Note | None:
        async with session.begin():
            return await repository.delete(
                session, id=race.note.id, owner_id=race.owner.id
            )

    assert await race(Note, {"owner_id": race.heir.id}, write) is None
    stored = await race.stored(Note)
    assert stored is not None
    assert stored.is_deleted is False


@pytest.mark.parametrize("write", ["update", "soft-delete"])
async def test_transactions_that_inserted_children_can_both_write_the_parent(
    race: Race, write: str
) -> None:
    """Each insert holds KEY SHARE on the owner. A lock stronger than the write
    needs waits for the other transaction's, and the two deadlock."""
    users = UserRepository()
    both_inserted = asyncio.Barrier(2)

    async def transaction(name: str) -> User | None:
        async with race.sessions() as session, session.begin():
            session.add(Note(title=name, content="", owner_id=race.owner.id))
            await session.flush()
            await both_inserted.wait()
            if write == "update":
                return await users.update(
                    session, {"first_name": name}, id=race.owner.id
                )
            return await users.delete(session, id=race.owner.id)

    async with asyncio.timeout(WAIT_SECONDS):
        results = await asyncio.gather(transaction("first"), transaction("second"))

    # The second soft delete waits, then finds no active row.
    assert results.count(None) == (0 if write == "update" else 1)
    async with race.sessions() as session:
        titles = await session.scalars(
            select(Note.title).where(Note.owner_id == race.owner.id)
        )
        assert sorted(titles) == ["Old", "first", "second"]
