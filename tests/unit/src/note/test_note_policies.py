from __future__ import annotations

import inspect
from uuid import uuid4

import pytest

from src.core.errors.exceptions import InstanceNotFoundException
from src.note import policies
from tests.factories.note_factory import build_note


def test_owner_without_permission_passes() -> None:
    owner_id = uuid4()
    note = build_note(owner_id=owner_id)

    policies.ensure_note_access(note, owner_id, has_permission=False)


def test_foreign_note_with_permission_passes() -> None:
    note = build_note(owner_id=uuid4())

    policies.ensure_note_access(note, uuid4(), has_permission=True)


def test_foreign_note_without_permission_raises_not_found() -> None:
    note = build_note(owner_id=uuid4())

    with pytest.raises(InstanceNotFoundException):
        policies.ensure_note_access(note, uuid4(), has_permission=False)


def test_policies_module_stays_pure() -> None:
    source = inspect.getsource(policies)
    import_lines = [
        line for line in source.splitlines() if line.startswith(("import ", "from "))
    ]
    for line in import_lines:
        assert not any(
            name in line for name in ("fastapi", "redis", "sqlalchemy")
        ), line
