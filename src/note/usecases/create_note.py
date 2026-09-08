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

    Ownership is taken from the authenticated caller, never from the request
    body, so a note cannot be created in someone else's name.
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
