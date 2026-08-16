from uuid import UUID

from src.core.errors.exceptions import InstanceNotFoundException
from src.note.models import Note


def ensure_note_access(note: Note, user_id: UUID, *, has_permission: bool) -> None:
    """Foreign note without permission answers 404 (anti-enumeration)."""
    if note.owner_id != user_id and not has_permission:
        raise InstanceNotFoundException("Note not found.")
