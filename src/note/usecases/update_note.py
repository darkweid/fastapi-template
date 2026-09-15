from typing import Annotated
from uuid import UUID

from fastapi import Depends

from loggers import get_logger
from src.core.database.session import get_unit_of_work
from src.core.database.uow import ApplicationUnitOfWork
from src.core.errors.exceptions import InstanceNotFoundException
from src.event_log.actor import Actor
from src.event_log.changes import changed_fields
from src.note.events import NoteUpdated
from src.note.policies import ensure_note_manage_access
from src.note.schemas import NoteUpdateModel, NoteViewModel
from src.user.models import User

logger = get_logger(__name__)


class UpdateNoteUseCase:
    """
    Update a note's title and content.

    PATCH semantics: an omitted field is left untouched (exclude_unset) and an
    explicit null fails schema validation, because both columns are
    non-nullable. A note the caller neither owns nor may manage answers the
    same 404 as a missing one.

    Side effects:
    - Appends `note.updated` to the event log when a field actually changed.
    """

    def __init__(self, uow: ApplicationUnitOfWork) -> None:
        self.uow = uow

    async def execute(
        self, note_id: UUID, data: NoteUpdateModel, current_user: User, actor: Actor
    ) -> NoteViewModel:
        update_data = data.model_dump(exclude_unset=True)
        async with self.uow as uow:
            note = await uow.notes.get_single(uow.session, id=note_id)
            if note is None:
                raise InstanceNotFoundException("Note not found.")
            ensure_note_manage_access(note, current_user.id, current_user.role)
            # Read off the loaded note before `update()` overwrites it in place.
            changes = changed_fields(note, update_data)

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
            if changes:
                # A PATCH that resends the stored values changed nothing, and a
                # row saying otherwise is what a reader would have to disprove.
                await uow.event_logs.record(
                    uow.session,
                    actor,
                    NoteUpdated(object_id=note_id, fields=sorted(changes)),
                )
            await uow.commit()
            logger.debug(
                "[UpdateNote] note %s updated by user %s.", note_id, current_user.id
            )
            return NoteViewModel.model_validate(updated_note)


def get_update_note_use_case(
    uow: Annotated[ApplicationUnitOfWork, Depends(get_unit_of_work)],
) -> UpdateNoteUseCase:
    return UpdateNoteUseCase(uow=uow)
