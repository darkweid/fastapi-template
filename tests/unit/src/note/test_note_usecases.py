from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.core.errors.exceptions import InstanceNotFoundException
from src.note.schemas import NoteCreateModel, NoteUpdateModel
from src.note.usecases.create_note import CreateNoteUseCase
from src.note.usecases.delete_note import DeleteNoteUseCase
from src.note.usecases.update_note import UpdateNoteUseCase
from src.user.enums import UserRole
from tests.factories.note_factory import build_note
from tests.factories.user_factory import build_user
from tests.fakes.db import FakeAsyncSession, FakeUnitOfWork


class FakeNotesRepository:
    def __init__(
        self,
        *,
        note=None,
        created_note=None,
        updated_note=None,
        deleted_note=None,
    ) -> None:
        self.get_single = AsyncMock(return_value=note)
        self.create = AsyncMock(return_value=created_note)
        self.update = AsyncMock(return_value=updated_note)
        self.delete = AsyncMock(return_value=deleted_note)


def build_uow(
    session: FakeAsyncSession, notes_repo: FakeNotesRepository
) -> FakeUnitOfWork:
    return FakeUnitOfWork(session=session, repositories={"notes": notes_repo})


@pytest.mark.asyncio
async def test_create_note_sets_owner(fake_session: FakeAsyncSession) -> None:
    owner_id = uuid4()
    created_note = build_note(owner_id=owner_id, title="My note")
    notes_repo = FakeNotesRepository(created_note=created_note)
    uow = build_uow(fake_session, notes_repo)
    use_case = CreateNoteUseCase(uow=uow)

    result = await use_case.execute(
        data=NoteCreateModel(title="My note"), owner_id=owner_id
    )

    assert result.owner_id == owner_id
    create_call = notes_repo.create.await_args
    assert create_call.args[1]["owner_id"] == owner_id
    uow.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_note_raises_not_found_when_missing(
    fake_session: FakeAsyncSession,
) -> None:
    notes_repo = FakeNotesRepository(note=None)
    uow = build_uow(fake_session, notes_repo)
    use_case = UpdateNoteUseCase(uow=uow)
    current_user = build_user(role=UserRole.ADMIN)

    with pytest.raises(InstanceNotFoundException):
        await use_case.execute(
            note_id=uuid4(),
            data=NoteUpdateModel(title="new title"),
            current_user=current_user,
        )

    uow.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_note_on_foreign_note_without_permission_raises_not_found(
    fake_session: FakeAsyncSession,
) -> None:
    note = build_note(owner_id=uuid4())
    notes_repo = FakeNotesRepository(note=note)
    uow = build_uow(fake_session, notes_repo)
    use_case = UpdateNoteUseCase(uow=uow)
    current_user = build_user(role=UserRole.VIEWER)

    with pytest.raises(InstanceNotFoundException):
        await use_case.execute(
            note_id=note.id,
            data=NoteUpdateModel(title="new title"),
            current_user=current_user,
        )

    uow.commit.assert_not_awaited()
    notes_repo.update.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_note_owner_succeeds(fake_session: FakeAsyncSession) -> None:
    current_user = build_user(role=UserRole.VIEWER)
    note = build_note(owner_id=current_user.id, title="Old title")
    updated_note = build_note(
        note_id=note.id, owner_id=current_user.id, title="New title"
    )
    notes_repo = FakeNotesRepository(note=note, updated_note=updated_note)
    uow = build_uow(fake_session, notes_repo)
    use_case = UpdateNoteUseCase(uow=uow)

    result = await use_case.execute(
        note_id=note.id,
        data=NoteUpdateModel(title="New title"),
        current_user=current_user,
    )

    assert result.title == "New title"
    uow.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_update_note_on_foreign_note_with_manage_permission_succeeds(
    fake_session: FakeAsyncSession,
) -> None:
    note = build_note(owner_id=uuid4(), title="Old title")
    updated_note = build_note(note_id=note.id, owner_id=note.owner_id, title="Updated")
    notes_repo = FakeNotesRepository(note=note, updated_note=updated_note)
    uow = build_uow(fake_session, notes_repo)
    use_case = UpdateNoteUseCase(uow=uow)
    admin_user = build_user(role=UserRole.ADMIN)

    result = await use_case.execute(
        note_id=note.id,
        data=NoteUpdateModel(title="Updated"),
        current_user=admin_user,
    )

    assert result.title == "Updated"
    uow.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_note_raises_not_found_when_missing(
    fake_session: FakeAsyncSession,
) -> None:
    notes_repo = FakeNotesRepository(note=None)
    uow = build_uow(fake_session, notes_repo)
    use_case = DeleteNoteUseCase(uow=uow)
    current_user = build_user(role=UserRole.ADMIN)

    with pytest.raises(InstanceNotFoundException):
        await use_case.execute(note_id=uuid4(), current_user=current_user)

    uow.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_note_on_foreign_note_without_permission_raises_not_found(
    fake_session: FakeAsyncSession,
) -> None:
    note = build_note(owner_id=uuid4())
    notes_repo = FakeNotesRepository(note=note)
    uow = build_uow(fake_session, notes_repo)
    use_case = DeleteNoteUseCase(uow=uow)
    current_user = build_user(role=UserRole.VIEWER)

    with pytest.raises(InstanceNotFoundException):
        await use_case.execute(note_id=note.id, current_user=current_user)

    uow.commit.assert_not_awaited()
    notes_repo.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_note_owner_succeeds(fake_session: FakeAsyncSession) -> None:
    current_user = build_user(role=UserRole.VIEWER)
    note = build_note(owner_id=current_user.id)
    notes_repo = FakeNotesRepository(note=note, deleted_note=note)
    uow = build_uow(fake_session, notes_repo)
    use_case = DeleteNoteUseCase(uow=uow)

    await use_case.execute(note_id=note.id, current_user=current_user)

    uow.commit.assert_awaited_once()
