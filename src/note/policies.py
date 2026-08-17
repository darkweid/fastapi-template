from uuid import UUID

from src.core.errors.exceptions import InstanceNotFoundException
from src.note.models import Note
from src.user.auth.permissions.enum import Permission
from src.user.auth.permissions.role_matrix import has_permission
from src.user.enums import UserRole


def _note_access_denied(
    note: Note, user_id: UUID, *, has_role_permission: bool
) -> bool:
    return note.owner_id != user_id and not has_role_permission


def ensure_note_view_access(note: Note, user_id: UUID, role: UserRole) -> None:
    """Foreign note without VIEW_NOTES answers 404 (anti-enumeration)."""
    if _note_access_denied(
        note, user_id, has_role_permission=has_permission(role, Permission.VIEW_NOTES)
    ):
        raise InstanceNotFoundException("Note not found.")


def ensure_note_manage_access(note: Note, user_id: UUID, role: UserRole) -> None:
    """Foreign note without MANAGE_NOTES answers 404 (anti-enumeration)."""
    if _note_access_denied(
        note, user_id, has_role_permission=has_permission(role, Permission.MANAGE_NOTES)
    ):
        raise InstanceNotFoundException("Note not found.")
