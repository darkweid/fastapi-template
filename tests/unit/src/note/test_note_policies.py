from __future__ import annotations

import inspect
from uuid import uuid4

import pytest

from src.core.errors.exceptions import InstanceNotFoundException
from src.note import policies
from src.user.enums import UserRole
from tests.factories.note_factory import build_note


def test_owner_without_permission_passes_view_and_manage() -> None:
    owner_id = uuid4()
    note = build_note(owner_id=owner_id)

    policies.ensure_note_view_access(note, owner_id, UserRole.VIEWER)
    policies.ensure_note_manage_access(note, owner_id, UserRole.VIEWER)


def test_foreign_note_with_permission_passes() -> None:
    note = build_note(owner_id=uuid4())

    policies.ensure_note_view_access(note, uuid4(), UserRole.ADMIN)
    policies.ensure_note_manage_access(note, uuid4(), UserRole.ADMIN)


def test_foreign_note_without_permission_raises_not_found() -> None:
    note = build_note(owner_id=uuid4())

    with pytest.raises(InstanceNotFoundException):
        policies.ensure_note_view_access(note, uuid4(), UserRole.VIEWER)
    with pytest.raises(InstanceNotFoundException):
        policies.ensure_note_manage_access(note, uuid4(), UserRole.VIEWER)


def test_policies_module_stays_pure() -> None:
    source = inspect.getsource(policies)
    import_lines = [
        line for line in source.splitlines() if line.startswith(("import ", "from "))
    ]
    for line in import_lines:
        assert not any(
            name in line for name in ("fastapi", "redis", "sqlalchemy")
        ), line
