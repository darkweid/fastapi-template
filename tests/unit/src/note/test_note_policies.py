from __future__ import annotations

import inspect
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.core.errors.exceptions import InstanceNotFoundException
from src.note import policies
from src.note.repositories import NoteRepository
from src.note.services import NoteService
from src.user.enums import UserRole
from tests.factories.note_factory import build_note
from tests.fakes.db import FakeAsyncSession


def test_owner_without_permission_passes_view_and_manage() -> None:
    owner_id = uuid4()
    note = build_note(owner_id=owner_id)

    policies.ensure_note_view_access(note.owner_id, owner_id, UserRole.VIEWER)
    policies.ensure_note_manage_access(note.owner_id, owner_id, UserRole.VIEWER)


def test_foreign_note_with_permission_passes() -> None:
    note = build_note(owner_id=uuid4())

    policies.ensure_note_view_access(note.owner_id, uuid4(), UserRole.ADMIN)
    policies.ensure_note_manage_access(note.owner_id, uuid4(), UserRole.ADMIN)


def test_foreign_note_without_permission_raises_not_found() -> None:
    note = build_note(owner_id=uuid4())

    with pytest.raises(InstanceNotFoundException):
        policies.ensure_note_view_access(note.owner_id, uuid4(), UserRole.VIEWER)
    with pytest.raises(InstanceNotFoundException):
        policies.ensure_note_manage_access(note.owner_id, uuid4(), UserRole.VIEWER)


def test_policies_module_stays_pure() -> None:
    source = inspect.getsource(policies)
    import_lines = [
        line for line in source.splitlines() if line.startswith(("import ", "from "))
    ]
    for line in import_lines:
        assert not any(
            name in line for name in ("fastapi", "redis", "sqlalchemy", ".models")
        ), line


async def test_a_missing_note_and_a_foreign_one_answer_the_same_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single differing character tells the caller the note exists."""
    repository = NoteRepository()
    monkeypatch.setattr(repository, "get_single", AsyncMock(return_value=None))

    with pytest.raises(InstanceNotFoundException) as missing:
        await NoteService(repository, FakeAsyncSession()).get_single_or_404(id=uuid4())
    with pytest.raises(InstanceNotFoundException) as foreign:
        policies.ensure_note_view_access(uuid4(), uuid4(), UserRole.VIEWER)

    assert str(missing.value) == str(foreign.value)


def test_a_note_is_not_printed_with_its_text() -> None:
    """A repr reaches log lines, and the title is the user's own text."""
    note = build_note(title="private title")

    assert "private title" not in repr(note)
    assert str(note.id) in repr(note)
