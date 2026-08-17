from typing import Annotated
from uuid import UUID

from fastapi import Depends

from loggers import get_logger
from src.core.database.session import get_unit_of_work
from src.core.database.uow import ApplicationUnitOfWork
from src.core.errors.exceptions import InstanceNotFoundException
from src.note.policies import ensure_note_manage_access
from src.note.schemas import NoteUpdateModel, NoteViewModel
from src.user.models import User

logger = get_logger(__name__)


class UpdateNoteUseCase:
    """
    Update a note's title and/or content.

    Inputs:
    - note_id: UUID of the note to update.
    - data: NoteUpdateModel with the fields to change; an omitted field is
      left untouched (exclude_unset), an explicit null fails schema validation.
    - current_user: the caller, used for the ownership/permission check.

    Validations:
    - The note must exist.
    - The caller must own the note or hold MANAGE_NOTES.

    Workflow:
    1) Load the note.
    2) Enforce ownership/permission via ensure_note_access.
    3) Apply the changed fields.
    4) Flush pending DB changes.
    5) Refresh the server-generated updated_at value.
    6) Commit the transaction.

    Side effects:
    - Updates the note record.

    Errors:
    - InstanceNotFoundException: if the note does not exist, or the caller
      neither owns it nor holds MANAGE_NOTES (anti-enumeration: both cases
      answer the same 404).

    Returns:
    - NoteViewModel: the updated note.
    """

    def __init__(self, uow: ApplicationUnitOfWork) -> None:
        self.uow = uow

    async def execute(
        self, note_id: UUID, data: NoteUpdateModel, current_user: User
    ) -> NoteViewModel:
        update_data = data.model_dump(exclude_unset=True)
        async with self.uow as uow:
            note = await uow.notes.get_single(uow.session, id=note_id)
            if note is None:
                raise InstanceNotFoundException("Note not found.")
            ensure_note_manage_access(note, current_user.id, current_user.role)

            updated_note = await uow.notes.update(uow.session, update_data, id=note_id)
            if updated_note is None:
                raise InstanceNotFoundException("Note not found.")
            await uow.flush()
            # updated_at has onupdate=func.now(), a server-side expression: the
            # UPDATE does not return its value, so SQLAlchemy leaves the attribute
            # expired after flush. Refreshing it here, inside the still-open
            # transaction, is required - reading it after commit instead would
            # need an implicit reload with no transaction/greenlet context left to
            # do it in, and serialization below would fail.
            await uow.session.refresh(updated_note, ["updated_at"])
            await uow.commit()
            logger.debug(
                "[UpdateNote] note %s updated by user %s.", note_id, current_user.id
            )
            return NoteViewModel.model_validate(updated_note)


def get_update_note_use_case(
    uow: Annotated[ApplicationUnitOfWork, Depends(get_unit_of_work)],
) -> UpdateNoteUseCase:
    return UpdateNoteUseCase(uow=uow)
