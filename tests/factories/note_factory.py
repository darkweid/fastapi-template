from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from src.core.utils.datetime_utils import get_utc_now
from src.note.models import Note


def build_note(
    *,
    note_id: UUID | None = None,
    owner_id: UUID | None = None,
    title: str = "Note title",
    content: str = "Note content",
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
    is_deleted: bool = False,
) -> Note:
    now = get_utc_now()
    return Note(
        id=note_id or uuid4(),
        owner_id=owner_id or uuid4(),
        title=title,
        content=content,
        created_at=created_at or now,
        updated_at=updated_at or now,
        is_deleted=is_deleted,
    )
