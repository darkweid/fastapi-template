from typing import Annotated
from uuid import UUID

from fastapi import Depends

from loggers import get_logger
from src.core.database.session import get_unit_of_work
from src.core.database.uow import ApplicationUnitOfWork, RepositoryProtocol
from src.core.errors.exceptions import InstanceNotFoundException
from src.note.policies import ensure_note_access
from src.note.schemas import NoteUpdateModel, NoteViewModel
from src.user.auth.permissions.enum import Permission
from src.user.auth.permissions.role_matrix import ROLE_PERMISSIONS
from src.user.models import User

logger = get_logger(__name__)


class UpdateNoteUseCase:
    """
    Update a note's title and/or content.

    Inputs:
    - note_id: UUID of the note to update.
    - data: NoteUpdateModel with the fields to change; an omitted field is
      left untouched.
    - current_user: the caller, used for the ownership/permission check.

    Validations:
    - The note must exist.
    - The caller must own the note or hold MANAGE_NOTES.

    Workflow:
    1) Load the note.
    2) Enforce ownership/permission via ensure_note_access.
    3) Apply the changed fields.
    4) Flush pending DB changes.
    5) Commit the transaction.

    Side effects:
    - Updates the note record.

    Errors:
    - InstanceNotFoundException: if the note does not exist, or the caller
      neither owns it nor holds MANAGE_NOTES (anti-enumeration: both cases
      answer the same 404).

    Returns:
    - NoteViewModel: the updated note.
    """

    def __init__(self, uow: ApplicationUnitOfWork[RepositoryProtocol]) -> None:
        self.uow = uow

    async def execute(
        self, note_id: UUID, data: NoteUpdateModel, current_user: User
    ) -> NoteViewModel:
        update_data = data.model_dump(exclude_none=True)
        async with self.uow as uow:
            note = await uow.notes.get_single(uow.session, id=note_id)
            if note is None:
                raise InstanceNotFoundException("Note not found.")
            has_permission = Permission.MANAGE_NOTES in ROLE_PERMISSIONS.get(
                current_user.role, set()
            )
            ensure_note_access(note, current_user.id, has_permission=has_permission)

            updated_note = await uow.notes.update(uow.session, update_data, id=note_id)
            if updated_note is None:
                raise InstanceNotFoundException("Note not found.")
            await uow.flush()
            await uow.commit()
            logger.debug(
                "[UpdateNote] note %s updated by user %s.", note_id, current_user.id
            )
            return NoteViewModel.model_validate(updated_note)


def get_update_note_use_case(
    uow: Annotated[
        ApplicationUnitOfWork[RepositoryProtocol], Depends(get_unit_of_work)
    ],
) -> UpdateNoteUseCase:
    return UpdateNoteUseCase(uow=uow)
