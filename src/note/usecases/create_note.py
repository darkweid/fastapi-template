from typing import Annotated
from uuid import UUID

from fastapi import Depends

from loggers import get_logger
from src.core.database.session import get_unit_of_work
from src.core.database.uow import ApplicationUnitOfWork
from src.note.schemas import NoteCreateModel, NoteViewModel

logger = get_logger(__name__)


class CreateNoteUseCase:
    """
    Create a note owned by the current user.

    Inputs:
    - data: NoteCreateModel with the title and optional content.
    - owner_id: UUID of the user the note will belong to.

    Validations:
    - None beyond schema validation; ownership is derived from the caller's
      identity, never taken from the request body.

    Workflow:
    1) Create the note row scoped to owner_id.
    2) Flush pending DB changes.
    3) Commit the transaction.

    Side effects:
    - Creates a new note record.

    Errors:
    - None beyond persistence failures surfaced by the repository.

    Returns:
    - NoteViewModel: the created note.
    """

    def __init__(self, uow: ApplicationUnitOfWork) -> None:
        self.uow = uow

    async def execute(self, data: NoteCreateModel, owner_id: UUID) -> NoteViewModel:
        create_data = data.model_dump()
        create_data["owner_id"] = owner_id
        async with self.uow as uow:
            note = await uow.notes.create(uow.session, create_data)
            await uow.flush()
            await uow.commit()
            logger.debug("[CreateNote] note %s created for user %s.", note.id, owner_id)
            return NoteViewModel.model_validate(note)


def get_create_note_use_case(
    uow: Annotated[ApplicationUnitOfWork, Depends(get_unit_of_work)],
) -> CreateNoteUseCase:
    return CreateNoteUseCase(uow=uow)
