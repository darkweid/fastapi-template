from typing import Annotated
from uuid import UUID

from fastapi import Depends

from loggers import get_logger
from src.core.database.session import get_unit_of_work
from src.core.database.uow import ApplicationUnitOfWork
from src.core.errors.exceptions import InstanceNotFoundException
from src.note.policies import ensure_note_manage_access
from src.user.models import User

logger = get_logger(__name__)


class DeleteNoteUseCase:
    """
    Soft-delete a note.

    Inputs:
    - note_id: UUID of the note to delete.
    - current_user: the caller, used for the ownership/permission check.

    Validations:
    - The note must exist.
    - The caller must own the note or hold MANAGE_NOTES.

    Workflow:
    1) Load the note.
    2) Enforce ownership/permission via ensure_note_access.
    3) Soft-delete the note.
    4) Flush pending DB changes.
    5) Commit the transaction.

    Side effects:
    - Marks the note as deleted (is_deleted, deleted_at).

    Errors:
    - InstanceNotFoundException: if the note does not exist, or the caller
      neither owns it nor holds MANAGE_NOTES (anti-enumeration: both cases
      answer the same 404).

    Returns:
    - None.
    """

    def __init__(self, uow: ApplicationUnitOfWork) -> None:
        self.uow = uow

    async def execute(self, note_id: UUID, current_user: User) -> None:
        async with self.uow as uow:
            note = await uow.notes.get_single(uow.session, id=note_id)
            if note is None:
                raise InstanceNotFoundException("Note not found.")
            ensure_note_manage_access(note, current_user.id, current_user.role)

            deleted_note = await uow.notes.delete(uow.session, id=note_id)
            if deleted_note is None:
                raise InstanceNotFoundException("Note not found.")
            await uow.flush()
            await uow.commit()
            logger.debug(
                "[DeleteNote] note %s deleted by user %s.", note_id, current_user.id
            )


def get_delete_note_use_case(
    uow: Annotated[ApplicationUnitOfWork, Depends(get_unit_of_work)],
) -> DeleteNoteUseCase:
    return DeleteNoteUseCase(uow=uow)
