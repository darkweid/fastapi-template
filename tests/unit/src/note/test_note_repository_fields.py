from __future__ import annotations

from src.note.repositories import NoteRepository


def test_note_repository_constructs_without_raising() -> None:
    repository = NoteRepository()

    assert repository.model.__name__ == "Note"
    assert repository.searchable_fields == ("title", "content")
    assert repository.sortable_fields == ("created_at", "title")
    assert repository.default_order_by == "created_at"
